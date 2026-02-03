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

# === Socratic Engine Integration ===
import json
from engine.socratic_engine import SocraticEngine

# Initializing Socratic Engine
print("Initializing Socratic Engine...")
with open('config/question_triggers_config.json', 'r') as f:
    SOCRATIC_CONFIG = json.load(f)

SOCRATIC_ENGINE = SocraticEngine(
    SOCRATIC_CONFIG,
    groq_api_key=os.environ.get('GROQ_API_KEY')  
)

USER_INTERACTION_HISTORY = {}
print("✓ Socratic Engine initialized successfully\n")

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
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        else:
            # For missing files, return 404 instead of crashing
            print(f"File not found: {file_path}")
            if fname == 'index.html':
                return web.Response(status=404, text="Application not found")
            else:
                return web.Response(status=404, text=f"File not found: {fname}")
    except Exception as e:
        print(f"Error serving file {fname}: {e}")
        return web.Response(status=500, text="Internal server error")

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
async def on_insight(sid, data):
    """Handle user insights from the frontend"""
    print(f"[DEBUG] on_insight received: {data}")
    
    # Check if this is a special operation (delete, edit) or regular insight
    operation_type = data.get("type", "create")
    
    if operation_type == "create":
        # Regular insight creation
        insight_text = data.get("text", "")
        timestamp = data.get("timestamp", datetime.now().isoformat())
        group = data.get("group", "interaction_trace")
        participant_id = data.get("participantId", "")
        
        # Create insight document
        insight = {
            "text": insight_text,
            "timestamp": timestamp,
            "group": group,
            "participant_id": participant_id,
            "operation": "create"
        }
        
        try:
            # Store in Firestore
            db.collection('insights').add(insight)
            print(f"[DEBUG] Stored insight in Firestore: {insight}")
            
            # Send confirmation back to client
            await SIO.emit("insight_saved", {"status": "success", "insight": insight}, room=sid)
            
        except Exception as e:
            print(f"[DEBUG] Error storing insight: {e}")
            await SIO.emit("insight_saved", {"status": "error", "message": str(e)}, room=sid)
    
    elif operation_type == "delete_insight":
        # Handle insight deletion
        participant_id = data.get("participantId", "")
        index = data.get("index", -1)
        timestamp = data.get("timestamp", datetime.now().isoformat())
        
        deletion_record = {
            "participant_id": participant_id,
            "index": index,
            "timestamp": timestamp,
            "operation": "delete"
        }
        
        try:
            # Store deletion record in Firestore
            db.collection('insight_operations').add(deletion_record)
            print(f"[DEBUG] Stored insight deletion in Firestore: {deletion_record}")
            
        except Exception as e:
            print(f"[DEBUG] Error storing insight deletion: {e}")
    
    elif operation_type == "edit_insight":
        # Handle insight editing
        participant_id = data.get("participantId", "")
        index = data.get("index", -1)
        old_text = data.get("oldText", "")
        new_text = data.get("newText", "")
        timestamp = data.get("timestamp", datetime.now().isoformat())
        
        edit_record = {
            "participant_id": participant_id,
            "index": index,
            "old_text": old_text,
            "new_text": new_text,
            "timestamp": timestamp,
            "operation": "edit"
        }
        
        try:
            # Store edit record in Firestore
            db.collection('insight_operations').add(edit_record)
            print(f"[DEBUG] Stored insight edit in Firestore: {edit_record}")
            
        except Exception as e:
            print(f"[DEBUG] Error storing insight edit: {e}")
    
    else:
        print(f"[DEBUG] Unknown insight operation type: {operation_type}")
        await SIO.emit("insight_saved", {"status": "error", "message": f"Unknown operation type: {operation_type}"}, room=sid)

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
    response["interactionId"] = data.get("interactionId")  # Include interaction ID for tracking
    response["participant_id"] = pid
    response["app_mode"] = app_mode
    response["app_type"] = app_type
    response["app_level"] = app_level
    response["processed_at"] = bias_util.get_current_time()
    response["interaction_type"] = interaction_type
    response["input_data"] = data

    # check whether to compute bias metrics or not
    send_response = False
    if interaction_type in COMPUTE_BIAS_FOR_TYPES:
        CLIENTS[pid]["bias_logs"].append(data)
        # Track interactions per socket ID for threshold checking
        SOCKET_INTERACTION_LOGS[sid]["bias_logs"].append(data)
        SOCKET_INTERACTION_LOGS[sid]["interaction_count"] += 1
        
        # Check if we have enough interactions for THIS SOCKET to show bias data
        socket_interaction_count = SOCKET_INTERACTION_LOGS[sid]["interaction_count"]
        has_enough_interactions = socket_interaction_count >= 20  # MIN_LOG_NUM
        
        # Only compute bias metrics if we have enough interactions
        if has_enough_interactions:
            try:
                # Use participant-based logs for bias computation (original Lumos approach)
                # Run bias computation asynchronously to prevent blocking
                import asyncio
                loop = asyncio.get_event_loop()
                metrics = await loop.run_in_executor(None, bias.compute_metrics, app_mode, CLIENTS[pid]["bias_logs"])
            except Exception as e:
                print(f"ERROR computing bias metrics: {e}")
                import traceback
                traceback.print_exc()
                # Send empty response to prevent client timeout
                response["output_data"] = {}
                await SIO.emit("interaction_response", response, room=sid)
                return
        else:
            # Send minimal response for early interactions
            metrics = {
                "data_point_distribution": [0, {"counts": {}}],
                "attribute_distribution": [{}, {}],
                "data_point_coverage": [{}, {}],
                "attribute_coverage": [{}, {}]
            }
        
        # For individual point interactions, only send back the updated point
        if interaction_type in ["mouseover_item", "mouseout_item", "click_item", "mouseover_group", "mouseout_group", "click_group"] and "data" in data and "id" in data["data"]:
            point_id = data["data"]["id"]
            
            # Check if this is a bar chart interaction by looking at the chart type or interaction type
            is_bar_chart_interaction = data.get("chartType") == "barchart" or interaction_type in ["mouseover_group", "mouseout_group", "click_group"]
            
            # Always send response for point interactions
            send_response = True
            
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
                    
                    # Include attribute data if we have enough interactions
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
                        
                        # Include attribute data if we have enough interactions
                        if has_enough_interactions:
                            modified_metrics["attribute_coverage"] = metrics["attribute_coverage"]
                            modified_metrics["attribute_distribution"] = metrics["attribute_distribution"]
                        
                        response["output_data"] = modified_metrics
                    else:
                        response["output_data"] = metrics
                else:
                    response["output_data"] = metrics
        else:
            # For non-point interactions, always send response if bias computation is enabled
            send_response = True
            response["output_data"] = metrics
        
    # Only send response back to the client if we have meaningful data
    if send_response:
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
        # Store in Firestore only if db is available
        if db is not None:
            db.collection('interactions').add(simplified_data)
        else:
            print("WARNING: Firestore not configured, skipping interaction storage")
    except Exception as e:
        print(f"ERROR storing interaction in Firestore: {e}")
        import traceback
        traceback.print_exc()

    # save response
    CLIENTS[pid]["response_list"].append(response)

    await SIO.emit("log", response)  # send this to all
    await SIO.emit("interaction_response", response, room=sid)

    # ========== Socratic Question Auto-Trigger ==========
    if SOCRATIC_ENGINE:  # Only execute if engine initialized successfully
        try:
            user_id = data.get("participantId") or sid
            
            # Initialize user interaction history
            if user_id not in USER_INTERACTION_HISTORY:
                USER_INTERACTION_HISTORY[user_id] = []
                print(f"\n[SOCRATIC] New user session: {user_id}")
            
            # Add current interaction to history
            USER_INTERACTION_HISTORY[user_id].append(data)
            
            # === Detailed debugging ===
            history_len = len(USER_INTERACTION_HISTORY[user_id])
            interaction_type = data.get('interactionType')
            print(f"[SOCRATIC] User: {user_id[:8]}... | Step: {history_len} | Type: {interaction_type}")
            
            # Build current context from interaction data
            current_context = {
                'x_attribute': data.get('x_attribute'),
                'y_attribute': data.get('y_attribute'),
                'chart_type': data.get('chart_changed'),
                'filters_active': []
            }
            
            # Process interaction and check if question should be triggered
            result = await SOCRATIC_ENGINE.process_interaction(
                user_id,
                data,
                current_context
            )
            
            # If a question was triggered, send it to the frontend
            if result.get('should_ask'):
                question_payload = {
                    'questionId': f"auto_{datetime.now().timestamp()}",
                    'category': result['category'],
                    'question': result['question'],
                    'method': result['method'],
                    'triggerDetails': result.get('trigger_details', {}),
                    'sessionInfo': result.get('session_info', {})
                }
                
                # Emit question to frontend via Socket.IO
                await SIO.emit('socratic_question_triggered', question_payload, room=sid)
                
                print(f"\n{'='*60}")
                print(f"✓ SOCRATIC QUESTION TRIGGERED!")
                print(f"{'='*60}")
                print(f"  User: {user_id}")
                print(f"  Step: {history_len}")
                print(f"  Category: {result['category']}")
                print(f"  Question: {result['question'][:80]}...")
                print(f"  Method: {result['method']}")
                print(f"  Confidence: {result['trigger_details'].get('confidence', 0):.2f}")
                print(f"{'='*60}\n")
            else:
                # Show status every 10 steps
                if history_len % 10 == 0:
                    print(f"[SOCRATIC] Step {history_len}: {result.get('reason', 'checking...')}")
        
        except Exception as e:
            # Silent failure - don't break existing functionality
            print(f"✗ Socratic Engine error: {e}")
            import traceback
            traceback.print_exc()
    
    # ========== Socratic Question Auto-Trigger ==========
    if SOCRATIC_ENGINE:  # Only execute if engine initialized successfully
        try:
            user_id = data.get("participantId") or sid
            
            # Initialize user interaction history
            if user_id not in USER_INTERACTION_HISTORY:
                USER_INTERACTION_HISTORY[user_id] = []
            
            # Add current interaction to history
            USER_INTERACTION_HISTORY[user_id].append(data)
            
            # Build current context from interaction data
            current_context = {
                'x_attribute': data.get('x_attribute'),
                'y_attribute': data.get('y_attribute'),
                'chart_type': data.get('chart_changed'),
                'filters_active': []
            }
            
            # Process interaction and check if question should be triggered
            result = await SOCRATIC_ENGINE.process_interaction(
                user_id,
                data,
                current_context
            )
            
            # If a question was triggered, send it to the frontend
            if result.get('should_ask'):
                question_payload = {
                    'questionId': f"auto_{datetime.now().timestamp()}",
                    'category': result['category'],
                    'question': result['question'],
                    'method': result['method'],
                    'triggerDetails': result.get('trigger_details', {}),
                    'sessionInfo': result.get('session_info', {})
                }
                
                # Emit question to frontend via Socket.IO
                await SIO.emit('socratic_question_triggered', question_payload, room=sid)
                
                print(f"\n✓ Socratic Question Triggered:")
                print(f"  User: {user_id}")
                print(f"  Category: {result['category']}")
                print(f"  Question: {result['question'][:80]}...")
                print(f"  Method: {result['method']}")
        
        except Exception as e:
            # Silent failure - don't break existing functionality
            print(f"✗ Socratic Engine error: {e}")


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
    """Handle interactions from the frontend (correct spelling)"""
    print(f"[DEBUG] recieve_interaction received: {data}")
    
    # Forward to the on_interaction handler
    await on_interaction(sid, data)

if __name__ == "__main__":
    bias.precompute_distributions()
    port = int(os.environ.get("PORT", 3000))
    web.run_app(APP, port=port)

    