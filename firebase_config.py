import os
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

_db = None

firebaseConfig = {
  "apiKey": "AIzaSyDI_XbMJgTUbehDoxhMQIOdD6joiBjLqFU",
  "authDomain": "mindtest-94298.firebaseapp.com",
  "projectId": "mindtest-94298",
  "storageBucket": "mindtest-94298.firebasestorage.app",
  "messagingSenderId": "620046306833",
  "appId": "1:620046306833:web:aea27de390e3c9ebf32bb0",
  "measurementId": "G-1ZLD1GX15N"
}

def get_db():
    global _db
    if _db is not None:
        return _db
    
    # Initialize Firebase if not already initialized
    if not firebase_admin._apps:
        cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, {"projectId": firebaseConfig["projectId"]})
            print(f"Firebase initialized with admin credentials from {cred_path}")
        else:
            # Fallback initialization using the provided config project ID
            print("WARNING: FIREBASE_CREDENTIALS_PATH not set or file not found. Attempting default initialization with firebaseConfig.")
            try:
                # Use default credentials but specify the project ID
                firebase_admin.initialize_app(options={"projectId": firebaseConfig["projectId"]})
                print(f"Firebase initialized with project ID: {firebaseConfig['projectId']}")
            except Exception as e:
                print(f"Failed to initialize Firebase: {e}")
                return None
    
    _db = firestore.client()
    return _db

# Immediate initialization of db instance so it can be imported
db = get_db()
