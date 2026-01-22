import os
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# Get Firebase credentials from environment variable
firebase_credentials = os.getenv('FIREBASE_CREDENTIALS')

if firebase_credentials:
    # Parse the credentials JSON string
    cred_dict = json.loads(firebase_credentials)
    cred = credentials.Certificate(cred_dict)
    # Initialize Firebase
    firebase_admin.initialize_app(cred)
    # Get Firestore client
    db = firestore.client()
else:
    # For development without Firebase
    print("Warning: FIREBASE_CREDENTIALS not set. Firebase functionality will be disabled.")
    db = None

# Define the schema for insights collection
insights_schema = {
    "text": str,           # insight text
    "timestamp": str,      # ISO timestamp
    "group": str,          # group identifier
    "participant_id": str  # user who provided insight
}

# Define the schema for interactions collection
interactions_schema = {
    "participant_id": str,     # user identifier
    "interaction_type": str,   # type of interaction
    "interacted_value": dict,  # interaction data
    "group": str,             # group identifier
    "timestamp": str          # ISO timestamp
} 