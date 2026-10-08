import os
import json
import requests

BASE_URL = "https://api.capafy.ai"

TOKEN = os.getenv("CAPAFY_ACCESS_TOKEN")

if not TOKEN:
    raise RuntimeError("❌ CAPAFY_ACCESS_TOKEN غير موجود في Environment Variables")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
}

def pretty(title, data):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def request(method, path, **kwargs):
    url = BASE_URL + path

    print(f"\n➡️ {method} {path}")

    try:
        response = requests.request(
            method,
            url,
            headers=HEADERS,
            timeout=30,
            **kwargs
        )

        print(f"HTTP {response.status_code}")

        try:
            data = response.json()
        except Exception:
            data = {
                "raw": response.text[:5000]
            }

        return response.status_code, data

    except Exception as e:
        print(f"❌ Request error: {e}")
        return None, {"error": str(e)}


# ============================================================
# 1. SEARCH CLONECUT
# ============================================================

query = (
    "AI video generation Seedance 2.0 "
    "text to video image to video character consistency "
    "short videos"
)

status, search_data = request(
    "POST",
    "/agent/agents/search",
    params={
        "query": query,
        "page": 1,
        "pageSize": 10,
    }
)

pretty("🔎 CLONECUT SEARCH", search_data)

agents = []

if isinstance(search_data, dict):
    data = search_data.get("data") or {}
    agents = data.get("list") or []

print(f"\nFound agents: {len(agents)}")

clonecut = None

for i, agent in enumerate(agents, 1):
    title = agent.get("title")
    agent_id = agent.get("agentId")
    agent_type = agent.get("agentType")
    score = agent.get("score")
    rating = agent.get("rating")
    sales = agent.get("salesVolume")

    print(
        f"\n[{i}] {title}\n"
        f"    agentId: {agent_id}\n"
        f"    type: {agent_type}\n"
        f"    score: {score}\n"
        f"    rating: {rating}\n"
        f"    sales: {sales}"
    )

    text = json.dumps(agent, ensure_ascii=False).lower()

    if "clonecut" in text:
        clonecut = agent


# ============================================================
# 2. GET CLONECUT DETAILS
# ============================================================

if clonecut:
    agent_id = clonecut.get("agentId")

    print("\n✅ CloneCut found!")
    print("Agent ID:", agent_id)

    status, detail_data = request(
        "GET",
        f"/agent/agent/agents/{agent_id}"
    )

    pretty("🎬 CLONECUT DETAILS", detail_data)

else:
    agent_id = None
    print("\n⚠️ لم نجد كلمة CloneCut في نتائج البحث.")
    print("هذا لا يعني بالضرورة أنه غير موجود؛ قد يكون ترتيب البحث مختلفًا.")


# ============================================================
# 3. ACTIVE INSTANCES
# ============================================================

status, active_data = request(
    "GET",
    "/agent/instance",
    params={
        "status": "active"
    }
)

pretty("🟢 ACTIVE INSTANCES", active_data)

active_instances = []

if isinstance(active_data, dict):
    data = active_data.get("data") or {}
    active_instances = data.get("instances") or []

print(f"\nActive instances: {len(active_instances)}")

for inst in active_instances:
    print(
        "\nINSTANCE"
        f"\n  instanceId: {inst.get('instanceId')}"
        f"\n  agentId:    {inst.get('agentId')}"
        f"\n  title:      {inst.get('agentTitle')}"
        f"\n  name:       {inst.get('name')}"
        f"\n  status:     {inst.get('status')}"
        f"\n  expiresAt:  {inst.get('expiresAt')}"
    )


# ============================================================
# 4. CHECK WHETHER ACTIVE CLONECUT INSTANCE EXISTS
# ============================================================

matching_instance = None

if agent_id:
    for inst in active_instances:
        if inst.get("agentId") == agent_id:
            matching_instance = inst
            break

if matching_instance:
    print("\n" + "=" * 70)
    print("🎉 FOUND ACTIVE CLONECUT INSTANCE")
    print("=" * 70)

    print("Agent ID:   ", matching_instance.get("agentId"))
    print("Instance ID:", matching_instance.get("instanceId"))
    print("Title:      ", matching_instance.get("agentTitle"))
    print("Status:     ", matching_instance.get("status"))
    print("Expires:    ", matching_instance.get("expiresAt"))

else:
    print("\n❌ لا يوجد Active Instance مربوط بـ CloneCut.")


# ============================================================
# 5. EXPIRED INSTANCES
# ============================================================

status, expired_data = request(
    "GET",
    "/agent/instance",
    params={
        "status": "expired"
    }
)

pretty("🔴 EXPIRED INSTANCES", expired_data)

expired_instances = []

if isinstance(expired_data, dict):
    data = expired_data.get("data") or {}
    expired_instances = data.get("instances") or []

print(f"\nExpired instances: {len(expired_instances)}")

for inst in expired_instances:
    print(
        "\nEXPIRED INSTANCE"
        f"\n  instanceId: {inst.get('instanceId')}"
        f"\n  agentId:    {inst.get('agentId')}"
        f"\n  title:      {inst.get('agentTitle')}"
        f"\n  name:       {inst.get('name')}"
    )


# ============================================================
# 6. SUMMARY
# ============================================================

print("\n" + "=" * 70)
print("📋 FINAL SUMMARY")
print("=" * 70)

print("Token:                ✅ موجود")
print("CloneCut search:      ", "✅" if clonecut else "⚠️ غير مؤكد")
print("Active instances:     ", len(active_instances))
print("Expired instances:    ", len(expired_instances))

if matching_instance:
    print("\n🚀 النتيجة:")
    print("CloneCut عندك لديه Instance فعال.")
    print("instanceId =", matching_instance.get("instanceId"))
    print("\nنقدر ننتقل بعدها لاختبار A10.")
else:
    print("\n🟡 النتيجة:")
    print("ما في Active CloneCut Instance ظاهر حاليًا.")
    print("❗ لم يتم شراء أي شيء.")
    print("❗ لم يتم إنشاء أي Order.")
    print("❗ لم يتم خصم أي Credits.")
