import os
import sys
import time
from gradio_client import Client


SPACE = "numanajmal0/wan-video-api"


def main():
    token = os.getenv("HF_TOKEN", "").strip()

    if not token:
        print("❌ HF_TOKEN is missing")
        sys.exit(1)

    print("=" * 60)
    print("🚀 Hugging Face ZeroGPU TEST")
    print("=" * 60)

    print("\n🔐 HF_TOKEN: FOUND")

    try:
        print("🔌 Connecting to Space...")

        client = Client(
            SPACE,
            hf_token=token,
        )

        print("✅ Connected successfully")

    except Exception as e:
        print(f"❌ Connection failed: {type(e).__name__}")
        print(str(e))
        sys.exit(1)

    # --------------------------------------------------
    # API SCHEMA
    # --------------------------------------------------

    print("\n📡 Reading API schema...")

    try:
        api = client.view_api()

        print("\n" + "=" * 60)
        print("📋 API SCHEMA")
        print("=" * 60)

        print(api)

        print("=" * 60)

    except Exception as e:
        print(f"❌ Could not read API schema")
        print(f"{type(e).__name__}: {e}")
        sys.exit(1)

    # --------------------------------------------------
    # HEALTH
    # --------------------------------------------------

    print("\n❤️ Checking health endpoint...")

    try:
        result = client.predict(api_name="/health")

        print("✅ HEALTH RESPONSE:")
        print(result)

    except Exception as e:
        print("⚠️ Health check failed:")
        print(f"{type(e).__name__}: {e}")

    # --------------------------------------------------
    # MODELS
    # --------------------------------------------------

    print("\n🧠 Checking available models...")

    try:
        result = client.predict(api_name="/list_models")

        print("✅ MODELS:")
        print(result)

    except Exception as e:
        print("⚠️ Model list failed:")
        print(f"{type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print("✅ TEST FINISHED")
    print("=" * 60)

    print(
        "\nIMPORTANT:"
        "\nلم نشغل توليد فيديو."
        "\nلم نستهلك وقت GPU للتوليد."
        "\nالآن نحتاج فقط schema الحقيقي لـ /generate."
    )


if __name__ == "__main__":
    main()
