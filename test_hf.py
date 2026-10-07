import os
from gradio_client import Client

SPACE = "numanajmal0/wan-video-api"

print("========== HF TEST START ==========")
print("Connecting to:", SPACE)

try:
    client = Client(
        SPACE,
        hf_token=os.getenv("HF_TOKEN")
    )

    print("\n========== API ==========\n")
    api = client.view_api()
    print(api)

    print("\n========== DONE ==========\n")

except Exception as e:
    print("\n========== HF TEST ERROR ==========\n")
    print(type(e).__name__)
    print(str(e))
