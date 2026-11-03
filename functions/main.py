from firebase_admin import initialize_app, firestore
from firebase_functions import https_fn
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
import base64
import json
import os
from datetime import datetime

# Initialize Firebase Admin & Firestore
initialize_app()
db = firestore.client()

@https_fn.on_request()
def api(request):
    """Main HTTP entrypoint for Sialvi Print Licensing System on Firebase Cloud Functions."""
    path = request.path
    method = request.method

    # CORS Headers for browser/dashboard access
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
    }

    if method == "OPTIONS":
        return ("$", 204, headers)

    try:
        # 1. Client Check-In Endpoint
        if path.endswith("/checkin") and method == "POST":
            req_data = request.get_json() or {}
            hwid = req_data.get("hwid")
            license_key = req_data.get("license_key")

            if not hwid or not license_key:
                return (json.dumps({"error": "Missing hwid or license_key"}), 400, headers)

            # Check if license exists in Firestore
            lic_ref = db.collection("licenses").document(license_key).get()
            if not lic_ref.exists:
                return (json.dumps({"status": "INVALID", "message": "License key not found."}), 403, headers)

            lic_data = lic_ref.to_dict()
            if lic_data.get("status") != "ACTIVE":
                return (json.dumps({"status": "SUSPENDED", "message": "License is suspended or expired."}), 403, headers)

            # Register or update device check-in
            device_ref = db.collection("registered_devices").document(hwid)
            device_ref.set({
                "hwid": hwid,
                "license_key": license_key,
                "last_checkin": firestore.SERVER_TIMESTAMP,
                "status": "ACTIVE"
            }, merge=True)

            # Check for pending remote commands (e.g. SUSPEND, LOCK, WIPE)
            commands_ref = db.collection("remote_commands")\
                .where("hwid", "==", hwid)\
                .where("status", "==", "PENDING")\
                .stream()

            pending_commands = []
            for cmd in commands_ref:
                cmd_dict = cmd.to_dict()
                pending_commands.append({
                    "id": cmd.id,
                    "command": cmd_dict.get("command")
                })
                # Mark command as delivered/sent
                db.collection("remote_commands").document(cmd.id).update({"status": "SENT"})

            response_payload = {
                "status": "ACTIVE",
                "message": "Check-in successful",
                "commands": pending_commands
            }
            return (json.dumps(response_payload), 200, headers)

        # 2. Default fallback route
        return (json.dumps({"error": "Endpoint not found"}), 404, headers)

    except Exception as e:
        return (json.dumps({"error": str(e)}), 500, headers)