"""Server for interfacing with the frontend.
"""
import os
from pathlib import Path
from datetime import datetime

import pandas as pd
import socketio
from aiohttp import web
from aiohttp_index import IndexMiddleware
from firebase_config import db  # Import the Firestore client
from firebase_admin import credentials, firestore
import bias
import bias_util


# Set the path for the Google Cloud Logging logger
currdir = Path(__file__).parent.absolute()

CLIENTS = {}  # entire data map of all client data
CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING = {}
CLIENT_SOCKET_ID_PARTICIPANT_MAPPING = {}
# Add new tracking for socket-based interaction counts
SOCKET_INTERACTION_LOGS = {}  # Track interactions per socket ID
COMPUTE_BIAS_FOR_TYPES = [
    "mouseout_item",
    "mouseover_item", 
    "mouseout_group",
    "mouseover_group",
    "click_group",
    "click_add_item",
    "click_remove_item",
    "click_item"
]

SIO = socketio.AsyncServer(cors_allowed_origins='*')
APP = web.Application(middlewares=[IndexMiddleware()])
SIO.attach(APP)

async def handle_ui_files(request):
    # Extract the requested file name
    fname = request.match_info.get('fname', 'index.html')

    # Serve index.html for all routes that don't have a file extension
    if '.' not in fname:
        fname = 'index.html'

    # Define the public directory (similar to Flask's 'public' directory)
    public_dir = os.path.join(os.path.dirname(__file__), 'public')

    # Serve the file from the public directory
    file_path = os.path.join(public_dir, fname)

    try:
        return web.FileResponse(file_path)
    except FileNotFoundError:
        raise web.HTTPNotFound()

# Static file serving
APP.router.add_static('/static/', path=str(os.path.join(os.path.dirname(__file__), 'public')), name='static')

# Dynamic routing for all paths, similar to Flask's catch-all routes
APP.router.add_route('GET', '/{fname:.*}', handle_ui_files)

@SIO.event
async def connect(sid, environ):
    attr_dist = {}
    for filename in bias.DATA_MAP:
        dataset = bias.DATA_MAP[filename]
        attr_dist[filename] = dataset["distribution"]
    await SIO.emit("attribute_distribution", attr_dist, room=sid)


@SIO.event
def disconnect(sid):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            CLIENTS[pid]["disconnected_at"] = bias_util.get_current_time()

# Debug handler to catch all events
@SIO.event
async def message(sid, data):
    pass

@SIO.event
async def on_session_end_page_level_logs(sid, payload):
    pid = payload["participantId"]
    if pid in CLIENTS and "data" in payload:
        dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
        Path(dirname).mkdir(exist_ok=True) 
        filename = f"output/{CLIENTS[pid]['app_type']}/{pid}/session_end_page_logs_{pid}_{bias_util.get_current_time()}.tsv"
        df_to_save = pd.DataFrame(payload["data"])

        # persist to disk
        df_to_save.transpose().to_csv(filename, sep="\t")


@SIO.event
async def on_save_logs(sid, data):
    if sid in CLIENT_SOCKET_ID_PARTICIPANT_MAPPING:
        pid = CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid]
        if pid in CLIENTS:
            dirname = f"output/{CLIENTS[pid]['app_type']}/{pid}"
            Path(dirname).mkdir(exist_ok=True)
            filename = f"output/{CLIENTS[pid]['app_type']}/{pid}/logs_{pid}_{bias_util.get_current_time()}.tsv"
            df_to_save = pd.DataFrame(CLIENTS[pid]["response_list"])

            # persist to disk
            df_to_save.to_csv(filename, sep="\t")

@SIO.event
async def on_interaction(sid, data):
    app_mode = data["appMode"]  # The dataset that is being used, e.g. synthetic_voters_v14.csv
    app_type = data["appType"]  # CONTROL / AWARENESS / ADMIN
    app_level = data["appLevel"]  # live / practice
    pid = data["participantId"]
    interaction_type = data["interactionType"] # Interaction type - eg. hover, click

    # Let these get updated everytime an interaction occurs, to handle the
    #   worst case scenario of random restart of the server.
    CLIENT_SOCKET_ID_PARTICIPANT_MAPPING[sid] = pid
    CLIENT_PARTICIPANT_ID_SOCKET_ID_MAPPING[pid] = sid

    # Initialize socket-based interaction tracking
    if sid not in SOCKET_INTERACTION_LOGS:
        SOCKET_INTERACTION_LOGS[sid] = {
            "app_mode": app_mode,
            "app_type": app_type,
            "app_level": app_level,
            "participant_id": pid,
            "interaction_count": 0,
            "bias_logs": []
        }
        # Reset participant logs for new session
        if pid in CLIENTS:
            CLIENTS[pid]["bias_logs"] = []
            CLIENTS[pid]["response_list"] = []
    else:
        # Check if this is a new session (same socket but different app_mode/app_level)
        current_session = SOCKET_INTERACTION_LOGS[sid]
        if app_mode != current_session["app_mode"] or app_level != current_session["app_level"]:
            # Update session info and reset counts
            SOCKET_INTERACTION_LOGS[sid]["app_mode"] = app_mode
            SOCKET_INTERACTION_LOGS[sid]["app_type"] = app_type
            SOCKET_INTERACTION_LOGS[sid]["app_level"] = app_level
            SOCKET_INTERACTION_LOGS[sid]["bias_logs"] = []
            SOCKET_INTERACTION_LOGS[sid]["interaction_count"] = 0
            # Reset participant logs for session change
            if pid in CLIENTS:
                CLIENTS[pid]["bias_logs"] = []
                CLIENTS[pid]["response_list"] = []

    if pid not in CLIENTS:
        # new participant => establish data mapping for them!
        CLIENTS[pid] = {}
        CLIENTS[pid]["id"] = sid
        CLIENTS[pid]["participant_id"] = pid
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_type"] = app_type
        CLIENTS[pid]["app_level"] = app_level
        CLIENTS[pid]["connected_at"] = bias_util.get_current_time()
        CLIENTS[pid]["bias_logs"] = []
        CLIENTS[pid]["response_list"] = []

    # Update participant info if needed (but don't reset logs here - handled in socket initialization)
    if app_mode != CLIENTS[pid]["app_mode"] or app_level != CLIENTS[pid]["app_level"]:
        CLIENTS[pid]["app_mode"] = app_mode
        CLIENTS[pid]["app_level"] = app_level
        # Note: Log reset is handled in socket initialization above

    # record response to interaction
    response = {}
    response["sid"] = sid
    response["participant_id"] = pid
    response["app_mode"] = app_mode
    response["app_type"] = app_type
    response["app_level"] = app_level
    response["processed_at"] = bias_util.get_current_time()
    response["interaction_type"] = interaction_type
    response["input_data"] = data

    # check whether to compute bias metrics or not
    if interaction_type in COMPUTE_BIAS_FOR_TYPES:
        CLIENTS[pid]["bias_logs"].append(data)
        # Track interactions per socket ID for threshold checking
        SOCKET_INTERACTION_LOGS[sid]["bias_logs"].append(data)
        SOCKET_INTERACTION_LOGS[sid]["interaction_count"] += 1
        
        # Use participant-based logs for bias computation (original Lumos approach)
        metrics = bias.compute_metrics(app_mode, CLIENTS[pid]["bias_logs"])
        
        # For individual point interactions, only send back the updated point
        if interaction_type in ["mouseover_item", "mouseout_item", "click_item", "mouseover_group", "mouseout_group", "click_group"] and "data" in data and "id" in data["data"]:
            point_id = data["data"]["id"]
            
            # Check if this is a bar chart interaction by looking at the chart type or interaction type
            is_bar_chart_interaction = data.get("chartType") == "barchart" or interaction_type in ["mouseover_group", "mouseout_group", "click_group"]
            
            # Check if we have enough interactions for THIS SOCKET to show bias data
            socket_interaction_count = SOCKET_INTERACTION_LOGS[sid]["interaction_count"]
            has_enough_interactions = socket_interaction_count >= 20  # MIN_LOG_NUM
            
            if isinstance(point_id, list) or is_bar_chart_interaction:
                # BAR CHART: Send back only the points in the interacted bar
                if "data_point_distribution" in metrics and len(metrics["data_point_distribution"]) > 1:
                    all_counts = metrics["data_point_distribution"][1]["counts"]
                    
                    # For bar chart interactions, we should always have a list of point IDs
                    if isinstance(point_id, list):
                        # point_id is already the array of points in the bar
                        bar_points = {}
                        for pid in point_id:
                            if pid in all_counts:
                                bar_points[pid] = all_counts[pid]
                    else:
                        # This shouldn't happen for bar chart interactions, but just in case
                        bar_points = {}
                        if point_id in all_counts:
                            bar_points[point_id] = all_counts[point_id]
                    
                    modified_metrics = {
                        "data_point_coverage": metrics["data_point_coverage"],
                        "data_point_distribution": [
                            metrics["data_point_distribution"][0],  # Keep the metric value
                            {"counts": bar_points}  # Only the points in this bar
                        ]
                    }
                    
                    # Only include attribute data if THIS SOCKET has enough interactions
                    if has_enough_interactions:
                        modified_metrics["attribute_coverage"] = metrics["attribute_coverage"]
                        modified_metrics["attribute_distribution"] = metrics["attribute_distribution"]
                    
                    response["output_data"] = modified_metrics
                else:
                    response["output_data"] = metrics
            else:
                # SCATTER PLOT: Only send back the specific point
                if "data_point_distribution" in metrics and len(metrics["data_point_distribution"]) > 1:
                    all_counts = metrics["data_point_distribution"][1]["counts"]
                    if point_id in all_counts:
                        # Create a modified response with only the updated point
                        modified_metrics = {
                            "data_point_coverage": metrics["data_point_coverage"],
                            "data_point_distribution": [
                                metrics["data_point_distribution"][0],  # Keep the metric value
                                {"counts": {point_id: all_counts[point_id]}}  # Only the updated point
                            ]
                        }
                        
                        # Only include attribute data if THIS SOCKET has enough interactions
                        if has_enough_interactions:
                            modified_metrics["attribute_coverage"] = metrics["attribute_coverage"]
                            modified_metrics["attribute_distribution"] = metrics["attribute_distribution"]
                        
                        response["output_data"] = modified_metrics
                    else:
                        response["output_data"] = metrics
                else:
                    response["output_data"] = metrics
        else:
            response["output_data"] = metrics
        
    # Send response back to the client
    try:
        await SIO.emit("interaction_response", response, room=sid)
    except Exception as e:
        print(f"ERROR sending interaction_response: {e}")
        import traceback
        traceback.print_exc()
        
    # Create simplified interaction data
    simplified_data = {
        "participant_id": pid,
        "interaction_type": interaction_type,
        "interacted_value": data["data"],
        "group": data.get("group"),  # Read from frontend, default to "interaction_trace"
        "timestamp": data["interactionAt"]
    }
    try:
        # Store in Firestore
        db.collection('interactions').add(simplified_data)
    except Exception as e:
        print(f"ERROR storing interaction in Firestore: {e}")
        import traceback
        traceback.print_exc()




@SIO.event
async def receive_external_question(sid, question_data):
        # Get the question type from any of the fields we're sending
    question_type = (
        question_data.get("promptType") or 
        question_data.get("questionCategory") or 
        question_data.get("userSelectedType") or 
        "socratic"  # default fallback
    )
    
    formatted_question = {
        "type": question_type,
        "id": question_data.get("id", str(datetime.now().timestamp())),
        "text": question_data.get("text", ""),
        "timestamp": datetime.now().isoformat(),
    }
    
    # Store in Firestore
    try:
        # Store in Firestore
        db.collection('questions').add(formatted_question)
    except Exception as e:
        print(f"ERROR storing question in Firestore: {e}")
        import traceback
        traceback.print_exc()
    
    # Simple broadcast to all clients except sender
    await SIO.emit(
        "question", 
        formatted_question, 
        broadcast=True,
        include_self=False,  # Don't send back to sender
    )

@SIO.event
async def on_question_response(sid, data):
    response = {
        "question_id": data.get("question_id"),
        "question": data.get("question"),
        "response": data.get("response"),
        "participant_id": data.get("participant_id"),
        "timestamp": datetime.now().isoformat()
    }
    try:
        # Store in Firestore
        db.collection('responses').add(response)
        
    except Exception as e:
        print(f"ERROR storing response in Firestore: {e}")
        import traceback
        traceback.print_exc()

@SIO.event
async def on_insight(sid, data):
    insight = {
        "text": data.get("text"),
        "timestamp": data.get("timestamp"),
        "group": data.get("group"),
        "participant_id": data.get("participantId")
    }
    
    try:
        # Store in Firestore
        db.collection('insights').add(insight)
        
    except Exception as e:
        print(f"ERROR storing insight in Firestore: {e}")
        import traceback
        traceback.print_exc()

@SIO.event
async def recieve_interaction(sid, data):
    interaction_type = data["interactionType"] # Interaction type - eg. hover, click
    pid = data["participantId"]

    simplified_data = {
        "participant_id": pid,
        "interaction_type": interaction_type,
        "interacted_value": data["data"],
        "group": data["group"],
        "timestamp": data["interactionAt"]
    }
    try:
        # Store in Firestore
        db.collection('interactions').add(simplified_data)
    except Exception as e:
        print(f"ERROR storing interaction in Firestore: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    bias.precompute_distributions()
    port = int(os.environ.get("PORT", 3000))
    web.run_app(APP, port=port)

