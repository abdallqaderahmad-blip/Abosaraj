import os
from gradio_client import Client

SPACE = "numanajmal0/wan-video-api"

print("Connecting to:", SPACE)

client = Client(
    SPACE,
    hf_token=os.getenv("HF_TOKEN")
)

print("\n========== API ==========\n")

api = client.view_api()

print(api)

print("\n========== DONE ==========\n")
