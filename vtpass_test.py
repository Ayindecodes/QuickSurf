import requests
import base64

# Replace with your real VTpass sandbox email and API key
email = "talyamariette@gmail.com"
api_key = "15af87fcb861ce59ce4ba219d0e5bad0"

# Encode the credentials
auth = base64.b64encode(f"{email}:{api_key}".encode()).decode()

headers = {
    "Authorization": f"Basic {auth}",
    "Content-Type": "application/json"
}

# Hit VTpass sandbox services endpoint
response = requests.get("https://sandbox.vtpass.com/api/services", headers=headers)

print("Status Code:", response.status_code)
print("Response Text:", response.text)
