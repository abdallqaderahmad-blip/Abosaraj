import os
import re
import json
import uuid
import shutil
import logging
import tempfile
import subprocess
import asyncio
import threading
import time

import requests
import edge_tts

from flask import Flask, request, jsonify
from groq import Groq
from gradio_client import Client


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

PORT = int(os.getenv("PORT", "10000"))

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

HF_SPACE = os.getenv(
    "HF_SPACE",
    "numanajmal0/wan-video-api"
)


# =========================================================
# CAPAFY CONFIG
# =========================================================

CAPAFY_BASE_URL = "https://api.capafy.ai"


# =========================================================
# VIDEO SETTINGS
# =========================================================

SHOT_COUNT = 2
SHOT_DURATION = 5


# =========================================================
# WAN PRODUCTION SETTINGS
# =========================================================

GEN_WIDTH = 576
GEN_HEIGHT = 832
GEN_FRAMES = 81
GEN_STEPS = 20
GEN_GUIDANCE = 5.0
GEN_SEED = 0


# =========================================================
# WAN TEST SETTINGS
# =========================================================

TEST_GEN_WIDTH = 320
TEST_GEN_HEIGHT = 320
TEST_GEN_FRAMES = 21
TEST_GEN_STEPS = 5
TEST_GEN_GUIDANCE = 5.0
TEST_GEN_SEED = 0


# =========================================================
# WAN EXTRA PARAMETERS
# =========================================================

GEN_PARAM_10 = 1.0
GEN_PARAM_11 = ""

GEN_FPS = 16

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FINAL_FPS = 16

TTS_VOICE = "ar-SA-HamedNeural"

DEFAULT_CTA = "إذا عجبك الفيديو تابعنا للمزيد"


# =========================================================
# SAFETY / RETRY
# =========================================================

MAX_VIDEO_RETRIES = 2

GENERATION_LOCK = threading.Lock()

PROCESSED_UPDATES = set()

MAX_PROCESSED_UPDATES = 1000


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("abosaraj")


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# ERROR HELPERS
# =========================================================

def safe_error_text(error):

    text = str(error)

    for secret in [
        BOT_TOKEN,
        GROQ_API_KEY,
        HF_TOKEN,
        os.getenv("CAPAFY_ACCESS_TOKEN", "").strip(),
        os.getenv("CAPAFY_API_KEY", "").strip(),
        os.getenv("CAPAFY_TEST_KEY", "").strip()
    ]:

        if secret:

            text = text.replace(
                secret,
                "***"
            )

    return text


def is_hf_quota_error(error):

    text = safe_error_text(error).lower()

    quota_words = [
        "zerogpu quota",
        "free zerogpu quota",
        "quota",
        "exceeded your free",
        "try again in",
        "subscribe to hugging face pro",
        "daily quota",
        "usage limit",
        "rate limit"
    ]

    return any(
        word in text
        for word in quota_words
    )


def ensure_dir(path):

    os.makedirs(
        path,
        exist_ok=True
    )

    return path


# =========================================================
# CAPAFY API HELPER
# =========================================================

def capafy_request(
    method,
    path,
    **kwargs
):

    token = os.getenv(
        "CAPAFY_ACCESS_TOKEN",
        ""
    ).strip()

    if not token:

        raise RuntimeError(
            "CAPAFY_ACCESS_TOKEN غير موجود في Environment Variables"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    response = requests.request(
        method,
        CAPAFY_BASE_URL + path,
        headers=headers,
        timeout=30,
        **kwargs
    )

    try:

        data = response.json()

    except Exception:

        data = {
            "raw": response.text[:5000]
        }

    return response.status_code, data


# =========================================================
# CAPAFY SAFE TEST
# =========================================================

@app.route(
    "/capafy-test",
    methods=["GET"]
)
def capafy_test():

    debug_key = os.getenv(
        "CAPAFY_TEST_KEY",
        ""
    ).strip()

    supplied_key = request.args.get(
        "key",
        ""
    ).strip()

    if not debug_key:

        return jsonify({
            "ok": False,
            "error": "CAPAFY_TEST_KEY غير موجود في Render"
        }), 500

    if supplied_key != debug_key:

        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    result = {

        "ok": True,

        "token_present": bool(
            os.getenv(
                "CAPAFY_ACCESS_TOKEN",
                ""
            ).strip()
        ),

        "clonecut_search": None,

        "clonecut_details": None,

        "active_instances": [],

        "expired_instances": [],

        "matching_instance": None
    }

    try:

        # -------------------------------------------------
        # SEARCH CLONECUT
        # -------------------------------------------------

        query = (
            "CloneCut Viral Clone Seedance 2.0 "
            "AI video generation text to video "
            "image to video"
        )

        status, search_data = capafy_request(
            "POST",
            "/agent/agents/search",
            params={
                "query": query,
                "page": 1,
                "pageSize": 10
            }
        )

        agents = []

        if isinstance(
            search_data,
            dict
        ):

            data = search_data.get(
                "data"
            ) or {}

            agents = data.get(
                "list"
            ) or []

        result["clonecut_search"] = {

            "http_status": status,

            "count": len(
                agents
            ),

            "agents": []
        }

        clonecut = None

        for agent in agents:

            safe_agent = {

                "agentId": agent.get(
                    "agentId"
                ),

                "agentVersionId": agent.get(
                    "agentVersionId"
                ),

                "title": agent.get(
                    "title"
                ),

                "agentType": agent.get(
                    "agentType"
                ),

                "model": agent.get(
                    "model"
                ),

                "rating": agent.get(
                    "rating"
                ),

                "salesVolume": agent.get(
                    "salesVolume"
                ),

                "score": agent.get(
                    "score"
                ),

                "billings": agent.get(
                    "billings"
                )
            }

            result[
                "clonecut_search"
            ][
                "agents"
            ].append(
                safe_agent
            )

            searchable = json.dumps(
                agent,
                ensure_ascii=False
            ).lower()

            if (
                clonecut is None
                and
                "clonecut" in searchable
            ):

                clonecut = agent

        # -------------------------------------------------
        # CLONECUT DETAILS
        # -------------------------------------------------

        if clonecut:

            agent_id = clonecut.get(
                "agentId"
            )

            status, detail_data = capafy_request(
                "GET",
                f"/agent/agent/agents/{agent_id}"
            )

            result[
                "clonecut_details"
            ] = {

                "http_status": status,

                "data": detail_data
            }

        # -------------------------------------------------
        # ACTIVE INSTANCES
        # -------------------------------------------------

        status, active_data = capafy_request(
            "GET",
            "/agent/instance",
            params={
                "status": "active"
            }
        )

        active_instances = []

        if isinstance(
            active_data,
            dict
        ):

            data = active_data.get(
                "data"
            ) or {}

            active_instances = data.get(
                "instances"
            ) or []

        for inst in active_instances:

            result[
                "active_instances"
            ].append({

                "instanceId": inst.get(
                    "instanceId"
                ),

                "agentId": inst.get(
                    "agentId"
                ),

                "agentTitle": inst.get(
                    "agentTitle"
                ),

                "name": inst.get(
                    "name"
                ),

                "status": inst.get(
                    "status"
                ),

                "expiresAt": inst.get(
                    "expiresAt"
                )
            })

        # -------------------------------------------------
        # FIND ACTIVE CLONECUT INSTANCE
        # -------------------------------------------------

        if clonecut:

            clonecut_agent_id = clonecut.get(
                "agentId"
            )

            for inst in active_instances:

                if (
                    inst.get(
                        "agentId"
                    )
                    ==
                    clonecut_agent_id
                ):

                    result[
                        "matching_instance"
                    ] = {

                        "instanceId": inst.get(
                            "instanceId"
                        ),

                        "agentId": inst.get(
                            "agentId"
                        ),

                        "agentTitle": inst.get(
                            "agentTitle"
                        ),

                        "status": inst.get(
                            "status"
                        ),

                        "expiresAt": inst.get(
                            "expiresAt"
                        )
                    }

                    break

        # -------------------------------------------------
        # EXPIRED INSTANCES
        # -------------------------------------------------

        status, expired_data = capafy_request(
            "GET",
            "/agent/instance",
            params={
                "status": "expired"
            }
        )

        expired_instances = []

        if isinstance(
            expired_data,
            dict
        ):

            data = expired_data.get(
                "data"
            ) or {}

            expired_instances = data.get(
                "instances"
            ) or []

        for inst in expired_instances:

            result[
                "expired_instances"
            ].append({

                "instanceId": inst.get(
                    "instanceId"
                ),

                "agentId": inst.get(
                    "agentId"
                ),

                "agentTitle": inst.get(
                    "agentTitle"
                ),

                "name": inst.get(
                    "name"
                ),

                "status": inst.get(
                    "status"
                )
            })

        # -------------------------------------------------
        # SUMMARY
        # -------------------------------------------------

        result[
            "summary"
        ] = {

            "clonecut_found": bool(
                clonecut
            ),

            "active_instance_count": len(
                active_instances
            ),

            "expired_instance_count": len(
                expired_instances
            ),

            "matching_active_clonecut": bool(
                result[
                    "matching_instance"
                ]
            ),

            "purchase_created": False,

            "credits_spent": False
        }

        return jsonify(
            result
        )

    except Exception as error:

        log.error(
            "CAPAFY_TEST_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return jsonify({

            "ok": False,

            "error": safe_error_text(
                error
            ),

            "purchase_created": False,

            "credits_spent": False
        }), 500


# =========================================================
# COMMAND RUNNER
# =========================================================

def run_cmd(
    cmd,
    cwd=None,
    timeout=None
):

    log.info(
        "RUN_CMD=%s",
        " ".join(
            map(
                str,
                cmd
            )
        )
    )

    try:

        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )

    except subprocess.TimeoutExpired:

        log.error(
            "COMMAND_TIMEOUT=%s",
            " ".join(
                map(
                    str,
                    cmd
                )
            )
        )

        raise RuntimeError(
            "Command timed out"
        )

    except Exception as error:

        log.error(
            "COMMAND_EXCEPTION=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    if result.stdout:

        log.info(
            "COMMAND_STDOUT=%s",
            result.stdout[-4000:]
        )

    if result.stderr:

        log.info(
            "COMMAND_STDERR=%s",
            result.stderr[-4000:]
        )

    if result.returncode != 0:

        log.error(
            "COMMAND_ERROR=returncode=%s stderr=%s",
            result.returncode,
            result.stderr[-4000:]
        )

        raise RuntimeError(
            f"Command failed with code {result.returncode}"
        )

    return result


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(
    method,
    payload=None,
    files=None
):

    if not BOT_TOKEN:

        log.error(
            "BOT_TOKEN_MISSING"
        )

        return None

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=payload,
            files=files,
            timeout=120
        )

        if not response.ok:

            log.error(
                "TELEGRAM_API_ERROR "
                "method=%s status=%s body=%s",
                method,
                response.status_code,
                response.text[:4000]
            )

            return None

        try:

            return response.json()

        except Exception:

            return None

    except Exception as error:

        log.error(
            "TELEGRAM_REQUEST_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return None


def send_message(
    chat_id,
    text
):

    return telegram_api(
        "sendMessage",
        payload={
            "chat_id": chat_id,
            "text": str(text)
        }
    )


def send_video(
    chat_id,
    video_path,
    caption=None
):

    log.info(
        "SEND_VIDEO path=%s",
        video_path
    )

    payload = {
        "chat_id": chat_id
    }

    if caption:

        payload["caption"] = caption

    try:

        with open(
            video_path,
            "rb"
        ) as video_file:

            return telegram_api(
                "sendVideo",
                payload=payload,
                files={
                    "video": (
                        os.path.basename(
                            video_path
                        ),
                        video_file,
                        "video/mp4"
                    )
                }
            )

    except Exception as error:

        log.error(
            "SEND_VIDEO_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return None


# =========================================================
# GROQ JSON EXTRACTION
# =========================================================

def extract_json_object(raw):

    raw = str(
        raw
    ).strip()

    raw = re.sub(
        r"^```(?:json)?",
        "",
        raw,
        flags=re.IGNORECASE
    )

    raw = re.sub(
        r"```$",
        "",
        raw
    ).strip()

    try:

        return json.loads(
            raw
        )

    except Exception:

        pass

    start = raw.find(
        "{"
    )

    end = raw.rfind(
        "}"
    )

    if (
        start == -1
        or
        end == -1
        or
        end <= start
    ):

        raise ValueError(
            "Could not find JSON object in Groq response"
        )

    candidate = raw[
        start:end + 1
    ]

    return json.loads(
        candidate
    )


# =========================================================
# GROQ STORYBOARD
# =========================================================

def create_storyboard(
    user_idea
):

    log.info(
        "PHASE=STORYBOARD_START"
    )

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY is missing"
        )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    prompt = f"""
أنت كاتب سيناريو ومخرج أفلام قصيرة سينمائية.

حوّل فكرة المستخدم إلى فيديو قصير عمودي.

فكرة المستخدم:
{user_idea}

أخرج JSON فقط.

الشكل:

{{
  "title": "عنوان قصير",
  "hook": "جملة افتتاحية قوية",
  "narration": [
    "التعليق الصوتي للمشهد الأول",
    "التعليق الصوتي للمشهد الثاني"
  ],
  "cta": "دعوة قصيرة للمتابعة",
  "scenes": [
    {{
      "scene": 1,
      "prompt": "cinematic English visual prompt",
      "duration": 5
    }},
    {{
      "scene": 2,
      "prompt": "cinematic English visual prompt",
      "duration": 5
    }}
  ]
}}

القواعد:

- عدد المشاهد: {SHOT_COUNT}
- مدة المشهد تقريبًا {SHOT_DURATION} ثوانٍ.
- prompts باللغة الإنجليزية.
- cinematic realistic.
- الشخصيات يجب أن تبقى متناسقة بين المشاهد.
- المشاهد يجب أن تكون امتدادًا لبعضها.
- لا تضع نصوصًا داخل الفيديو.
- لا شعارات.
- لا Watermarks.
- لا تكتب شرحًا خارج JSON.
- اجعل أول مشهد يحتوي على Hook بصري قوي.
- اجعل نهاية آخر مشهد تدفع المشاهد لمعرفة ماذا سيحدث بعد ذلك.
"""

    try:

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional "
                        "cinematic director, screenwriter "
                        "and storyboard designer."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=3000
        )

        raw = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        log.info(
            "GROQ_RAW_RESPONSE=%s",
            raw[:10000]
        )

        data = extract_json_object(
            raw
        )

        if not isinstance(
            data,
            dict
        ):

            raise ValueError(
                "Storyboard is not an object"
            )

        scenes = data.get(
            "scenes"
        )

        if not isinstance(
            scenes,
            list
        ):

            raise ValueError(
                "scenes is not a list"
            )

        if not scenes:

            raise ValueError(
                "No scenes returned"
            )

        scenes = scenes[
            :SHOT_COUNT
        ]

        clean_scenes = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            if not isinstance(
                scene,
                dict
            ):

                continue

            prompt_text = str(
                scene.get(
                    "prompt",
                    ""
                )
            ).strip()

            if not prompt_text:

                continue

            clean_scenes.append(
                {
                    "scene": index,
                    "prompt": prompt_text,
                    "duration": SHOT_DURATION
                }
            )

        if not clean_scenes:

            raise ValueError(
                "No valid scenes after validation"
            )

        data["scenes"] = clean_scenes

        narration = data.get(
            "narration",
            []
        )

        if not isinstance(
            narration,
            list
        ):

            narration = []

        narration = [
            str(x).strip()
            for x in narration
        ]

        while len(
            narration
        ) < len(
            clean_scenes
        ):

            narration.append(
                ""
            )

        data["narration"] = narration[
            :len(clean_scenes)
        ]

        cta = str(
            data.get(
                "cta",
                DEFAULT_CTA
            )
        ).strip()

        data["cta"] = (
            cta
            or
            DEFAULT_CTA
        )

        log.info(
            "STORYBOARD_OK title=%s scenes=%s",
            data.get("title"),
            len(data["scenes"])
        )

        return data

    except Exception as error:

        log.error(
            "STORYBOARD_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise


# =========================================================
# HUGGING FACE CLIENT
# =========================================================

def create_hf_client():

    log.info(
        "HF_CLIENT_CREATE space=%s token=%s",
        HF_SPACE,
        bool(HF_TOKEN)
    )

    if HF_TOKEN:

        return Client(
            HF_SPACE,
            token=HF_TOKEN
        )

    return Client(
        HF_SPACE
    )


def get_hf_api_dict(
    client
):

    log.info(
        "HF_VIEW_API_START"
    )

    api = client.view_api(
        return_format="dict"
    )

    log.info(
        "HF_VIEW_API_DONE"
    )

    return api


def get_generate_endpoint(
    api
):

    named = api.get(
        "named_endpoints",
        {}
    )

    if "/generate" in named:

        return named[
            "/generate"
        ]

    for name, endpoint in named.items():

        if "generate" in name.lower():

            return endpoint

    raise RuntimeError(
        "Could not find /generate endpoint"
    )


# =========================================================
# HF SAFE CHECK
# =========================================================

def check_hf_space():

    log.info(
        "PHASE=HF_SAFE_CHECK_START"
    )

    try:

        client = create_hf_client()

        api = get_hf_api_dict(
            client
        )

        endpoint = get_generate_endpoint(
            api
        )

        params = endpoint.get(
            "parameters",
            []
        )

        if not params:

            raise RuntimeError(
                "Generate endpoint has no parameters"
            )

        log.info(
            "HF_SAFE_CHECK_OK parameters=%s",
            len(params)
        )

        return {
            "ok": True,
            "parameter_count": len(params)
        }

    except Exception as error:

        log.error(
            "HF_SAFE_CHECK_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return {
            "ok": False,
            "error": safe_error_text(error)
        }


# =========================================================
# WAN ARGUMENTS
# =========================================================

def build_wan_arguments(
    prompt,
    test_mode=False
):

    if test_mode:

        width = TEST_GEN_WIDTH
        height = TEST_GEN_HEIGHT
        frames = TEST_GEN_FRAMES
        steps = TEST_GEN_STEPS
        guidance = TEST_GEN_GUIDANCE
        seed = TEST_GEN_SEED

        log.info(
            "WAN_MODE=LOW_COST_TEST"
        )

    else:

        width = GEN_WIDTH
        height = GEN_HEIGHT
        frames = GEN_FRAMES
        steps = GEN_STEPS
        guidance = GEN_GUIDANCE
        seed = GEN_SEED

        log.info(
            "WAN_MODE=PRODUCTION"
        )

    args = [

        "wan-base",

        prompt,

        (
            "blurry, low quality, distorted, "
            "deformed, bad anatomy, watermark, "
            "text, logo"
        ),

        width,
        height,
        frames,
        steps,
        guidance,
        seed,
        GEN_PARAM_10,
        GEN_PARAM_11
    ]

    for index, value in enumerate(
        args,
        start=1
    ):

        if index == 2:

            log.info(
                "WAN_ARG_%s=%s",
                index,
                str(value)[:1500]
            )

        else:

            log.info(
                "WAN_ARG_%s=%r",
                index,
                value
            )

    return args


# =========================================================
# EXTRACT VIDEO SOURCE
# =========================================================

def extract_video_source(
    result
):

    log.info(
        "HF_RESULT_TYPE=%s",
        type(result).__name__
    )

    log.info(
        "HF_RESULT_REPR=%s",
        repr(result)[:6000]
    )

    if isinstance(
        result,
        str
    ):

        return result

    if isinstance(
        result,
        dict
    ):

        for key in [
            "video",
            "path",
            "url",
            "value",
            "data",
            "file"
        ]:

            if key not in result:

                continue

            value = result[
                key
            ]

            if isinstance(
                value,
                str
            ):

                return value

            if isinstance(
                value,
                dict
            ):

                nested = extract_video_source(
                    value
                )

                if nested:

                    return nested

        for value in result.values():

            if isinstance(
                value,
                str
            ):

                if (
                    value.startswith(
                        "http://"
                    )
                    or
                    value.startswith(
                        "https://"
                    )
                    or
                    os.path.exists(
                        value
                    )
                ):

                    return value

            if isinstance(
                value,
                dict
            ):

                nested = extract_video_source(
                    value
                )

                if nested:

                    return nested

    if isinstance(
        result,
        (list, tuple)
    ):

        for item in result:

            source = extract_video_source(
                item
            )

            if source:

                return source

    return None


# =========================================================
# DOWNLOAD
# =========================================================

def download_url(
    url,
    destination
):

    log.info(
        "HF_DOWNLOAD_START"
    )

    with requests.get(
        url,
        stream=True,
        timeout=300
    ) as response:

        response.raise_for_status()

        with open(
            destination,
            "wb"
        ) as output:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:

                    output.write(
                        chunk
                    )

    size = os.path.getsize(
        destination
    )

    log.info(
        "HF_DOWNLOAD_DONE size=%s",
        size
    )

    return destination


# =========================================================
# VIDEO VALIDATION
# =========================================================

def validate_video(
    path
):

    if not os.path.exists(
        path
    ):

        raise RuntimeError(
            "Video does not exist"
        )

    size = os.path.getsize(
        path
    )

    if size < 1000:

        raise RuntimeError(
            f"Video file too small: {size}"
        )

    try:

        result = run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path
            ],
            timeout=60
        )

        duration = float(
            result.stdout.strip()
        )

    except Exception as error:

        raise RuntimeError(
            "Video validation failed: "
            + safe_error_text(error)
        )

    if duration <= 0:

        raise RuntimeError(
            "Video duration is zero"
        )

    log.info(
        "VIDEO_VALIDATED duration=%.3f size=%s",
        duration,
        size
    )

    return {
        "duration": duration,
        "size": size
    }


# =========================================================
# GENERATE AI VIDEO
# =========================================================

def generate_ai_video_once(
    prompt,
    output_dir,
    test_mode=False
):

    check = check_hf_space()

    if not check["ok"]:

        raise RuntimeError(
            "HF_SAFE_CHECK_FAILED: "
            + check["error"]
        )

    client = create_hf_client()

    args = build_wan_arguments(
        prompt,
        test_mode=test_mode
    )

    log.info(
        "HF_PREDICT_START"
    )

    try:

        result = client.predict(
            *args,
            api_name="/generate"
        )

    except Exception as error:

        if is_hf_quota_error(
            error
        ):

            raise RuntimeError(
                "HF_QUOTA_EXHAUSTED: "
                + safe_error_text(error)
            )

        raise

    log.info(
        "HF_PREDICT_SUCCESS"
    )

    source = extract_video_source(
        result
    )

    if not source:

        raise RuntimeError(
            "HF returned no video source"
        )

    destination = os.path.join(
        output_dir,
        f"scene_{uuid.uuid4().hex}.mp4"
    )

    if (
        isinstance(source, str)
        and
        (
            source.startswith(
                "http://"
            )
            or
            source.startswith(
                "https://"
            )
        )
    ):

        download_url(
            source,
            destination
        )

    elif (
        isinstance(source, str)
        and
        os.path.exists(
            source
        )
    ):

        shutil.copy2(
            source,
            destination
        )

    else:

        raise RuntimeError(
            "Invalid HF video source: "
            + str(source)
        )

    validate_video(
        destination
    )

    return destination


def generate_ai_video(
    prompt,
    output_dir,
    test_mode=False
):

    log.info(
        "=" * 80
    )

    log.info(
        "PHASE=HF_VIDEO_GENERATION_START"
    )

    log.info(
        "HF_PROMPT=%s",
        prompt
    )

    log.info(
        "HF_TEST_MODE=%s",
        test_mode
    )

    ensure_dir(
        output_dir
    )

    last_error = None

    for attempt in range(
        1,
        MAX_VIDEO_RETRIES + 1
    ):

        log.info(
            "HF_GENERATION_ATTEMPT=%s/%s",
            attempt,
            MAX_VIDEO_RETRIES
        )

        try:

            result = generate_ai_video_once(
                prompt,
                output_dir,
                test_mode=test_mode
            )

            log.info(
                "PHASE=HF_VIDEO_GENERATION_DONE"
            )

            return result

        except Exception as error:

            last_error = error

            error_text = safe_error_text(
                error
            )

            log.error(
                "HF_GENERATION_ATTEMPT_ERROR=%s",
                error_text,
                exc_info=True
            )

            if is_hf_quota_error(
                error
            ):

                log.error(
                    "HF_QUOTA_NO_RETRY"
                )

                break

            if attempt < MAX_VIDEO_RETRIES:

                wait_seconds = attempt * 3

                log.info(
                    "HF_RETRY_WAIT=%s",
                    wait_seconds
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        "HF_GENERATION_FAILED: "
        + safe_error_text(
            last_error
        )
    )


# =========================================================
# VIDEO NORMALIZE
# =========================================================

def normalize_video(
    input_path,
    output_path
):

    log.info(
        "PHASE=VIDEO_NORMALIZE_START"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,

            "-vf",
            (
                f"scale={FINAL_WIDTH}:"
                f"{FINAL_HEIGHT}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={FINAL_WIDTH}:"
                f"{FINAL_HEIGHT}:"
                "(ow-iw)/2:"
                "(oh-ih)/2"
            ),

            "-r",
            str(FINAL_FPS),

            "-an",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-pix_fmt",
            "yuv420p",

            output_path
        ],
        timeout=600
    )

    validate_video(
        output_path
    )

    log.info(
        "PHASE=VIDEO_NORMALIZE_DONE"
    )

    return output_path


# =========================================================
# CONCAT VIDEOS
# =========================================================

def concat_videos(
    video_paths,
    output_path
):

    if not video_paths:

        raise RuntimeError(
            "No videos to concatenate"
        )

    log.info(
        "PHASE=VIDEO_CONCAT_START count=%s",
        len(video_paths)
    )

    list_file = (
        output_path
        + ".txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in video_paths:

            absolute = os.path.abspath(
                path
            )

            escaped = absolute.replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{escaped}'\n"
            )

    try:

        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                output_path
            ],
            timeout=900
        )

    finally:

        if os.path.exists(
            list_file
        ):

            os.remove(
                list_file
            )

    validate_video(
        output_path
    )

    log.info(
        "PHASE=VIDEO_CONCAT_DONE"
    )

    return output_path


# =========================================================
# TTS
# =========================================================

async def _tts(
    text,
    output_path
):

    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE
    )

    await communicate.save(
        output_path
    )


def generate_tts(
    text,
    output_path
):

    text = str(
        text
    ).strip()

    if not text:

        text = " "

    log.info(
        "PHASE=TTS_START text=%s",
        text
    )

    try:

        asyncio.run(
            _tts(
                text,
                output_path
            )
        )

    except Exception as error:

        log.error(
            "TTS_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    if not os.path.exists(
        output_path
    ):

        raise RuntimeError(
            "TTS file was not created"
        )

    size = os.path.getsize(
        output_path
    )

    if size < 100:

        raise RuntimeError(
            "TTS file is empty"
        )

    log.info(
        "PHASE=TTS_DONE size=%s",
        size
    )

    return output_path


# =========================================================
# DURATION
# =========================================================

def get_duration(
    path
):

    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path
        ],
        timeout=60
    )

    value = result.stdout.strip()

    if not value:

        raise RuntimeError(
            "Could not read duration"
        )

    return float(
        value
    )


# =========================================================
# AUDIO CONCAT
# =========================================================

def concat_audio(
    audio_paths,
    output_path
):

    if not audio_paths:

        raise RuntimeError(
            "No audio files"
        )

    list_file = (
        output_path
        + ".txt"
    )

    with open(
        list_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in audio_paths:

            absolute = os.path.abspath(
                path
            )

            escaped = absolute.replace(
                "'",
                "'\\''"
            )

            f.write(
                f"file '{escaped}'\n"
            )

    try:

        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                output_path
            ],
            timeout=600
        )

    finally:

        if os.path.exists(
            list_file
        ):

            os.remove(
                list_file
            )

    return output_path


# =========================================================
# CREATE SILENT AUDIO
# =========================================================

def create_silence(
    duration,
    output_path
):

    duration = max(
        0.1,
        float(duration)
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(duration),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            output_path
        ],
        timeout=120
    )

    return output_path


# =========================================================
# MUX
# =========================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
):

    log.info(
        "PHASE=MUX_START"
    )

    video_duration = get_duration(
        video_path
    )

    audio_duration = get_duration(
        audio_path
    )

    log.info(
        "MUX_DURATIONS video=%.3f audio=%.3f",
        video_duration,
        audio_duration
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",

            "-i",
            video_path,

            "-i",
            audio_path,

            "-map",
            "0:v:0",

            "-map",
            "1:a:0",

            "-c:v",
            "copy",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-t",
            str(video_duration),

            "-movflags",
            "+faststart",

            output_path
        ],
        timeout=900
    )

    validate_video(
        output_path
    )

    log.info(
        "PHASE=MUX_DONE"
    )

    return output_path


# =========================================================
# SRT
# =========================================================

def format_srt_time(
    seconds
):

    total_ms = int(
        round(
            float(seconds) * 1000
        )
    )

    hours = total_ms // 3600000

    minutes = (
        total_ms % 3600000
    ) // 60000

    secs = (
        total_ms % 60000
    ) // 1000

    milliseconds = (
        total_ms % 1000
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def create_srt(
    items,
    output_path
):

    current = 0.0

    subtitle_index = 1

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        for item in items:

            text = str(
                item.get(
                    "text",
                    ""
                )
            ).strip()

            duration = max(
                0.1,
                float(
                    item.get(
                        "duration",
                        0
                    )
                )
            )

            start = current

            end = (
                current
                + duration
            )

            if text:

                f.write(
                    f"{subtitle_index}\n"
                )

                f.write(
                    f"{format_srt_time(start)} --> "
                    f"{format_srt_time(end)}\n"
                )

                f.write(
                    text
                    + "\n\n"
                )

                subtitle_index += 1

            current = end

    return output_path


# =========================================================
# CAPTIONS
# =========================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):

    log.info(
        "PHASE=CAPTIONS_START"
    )

    subtitle_file = (
        os.path.abspath(
            srt_path
        )
        .replace(
            "\\",
            "/"
        )
        .replace(
            ":",
            "\\:"
        )
        .replace(
            "'",
            "\\'"
        )
    )

    subtitle_filter = (
        "subtitles='"
        + subtitle_file
        + "'"
    )

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path,

            "-vf",
            subtitle_filter,

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "20",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            output_path
        ],
        timeout=900
    )

    validate_video(
        output_path
    )

    log.info(
        "PHASE=CAPTIONS_DONE"
    )

    return output_path


# =========================================================
# CREATE REEL
# =========================================================

def create_reel(
    user_idea
):

    work_dir = tempfile.mkdtemp(
        prefix="abosaraj_"
    )

    log.info(
        "=" * 80
    )

    log.info(
        "PHASE=CREATE_REEL_START"
    )

    log.info(
        "WORK_DIR=%s",
        work_dir
    )

    try:

        storyboard = create_storyboard(
            user_idea
        )

        scenes = storyboard[
            "scenes"
        ]

        narration = storyboard.get(
            "narration",
            []
        )

        cta = str(
            storyboard.get(
                "cta",
                DEFAULT_CTA
            )
        ).strip()

        raw_dir = ensure_dir(
            os.path.join(
                work_dir,
                "raw"
            )
        )

        normalized_dir = ensure_dir(
            os.path.join(
                work_dir,
                "normalized"
            )
        )

        normalized_videos = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            log.info(
                "SCENE_%s_START",
                index
            )

            prompt = str(
                scene.get(
                    "prompt",
                    ""
                )
            ).strip()

            if not prompt:

                raise RuntimeError(
                    f"Scene {index} prompt is empty"
                )

            raw_video = generate_ai_video(
                prompt,
                raw_dir,
                test_mode=False
            )

            normalized = os.path.join(
                normalized_dir,
                f"scene_{index}.mp4"
            )

            normalize_video(
                raw_video,
                normalized
            )

            normalized_videos.append(
                normalized
            )

        concat_video = os.path.join(
            work_dir,
            "video_concat.mp4"
        )

        concat_videos(
            normalized_videos,
            concat_video
        )

        audio_dir = ensure_dir(
            os.path.join(
                work_dir,
                "audio"
            )
        )

        audio_files = []
        subtitle_items = []

        for index, scene in enumerate(
            scenes,
            start=1
        ):

            if index - 1 < len(
                narration
            ):

                narration_text = str(
                    narration[index - 1]
                ).strip()

            else:

                narration_text = ""

            if not narration_text:

                narration_text = " "

            audio_path = os.path.join(
                audio_dir,
                f"voice_{index}.mp3"
            )

            generate_tts(
                narration_text,
                audio_path
            )

            video_duration = get_duration(
                normalized_videos[
                    index - 1
                ]
            )

            audio_duration = get_duration(
                audio_path
            )

            audio_files.append(
                audio_path
            )

            subtitle_items.append(
                {
                    "text": narration_text,
                    "duration": video_duration
                }
            )

            log.info(
                "SCENE_AUDIO_%s video=%.3f audio=%.3f",
                index,
                video_duration,
                audio_duration
            )

        audio_concat = os.path.join(
            work_dir,
            "audio.m4a"
        )

        concat_audio(
            audio_files,
            audio_concat
        )

        video_duration = get_duration(
            concat_video
        )

        audio_duration = get_duration(
            audio_concat
        )

        log.info(
            "FINAL_AV_LENGTH video=%.3f audio=%.3f",
            video_duration,
            audio_duration
        )

        if audio_duration < video_duration:

            silence_path = os.path.join(
                audio_dir,
                "padding_silence.m4a"
            )

            create_silence(
                video_duration - audio_duration,
                silence_path
            )

            padded_audio = os.path.join(
                work_dir,
                "audio_padded.m4a"
            )

            concat_audio(
                [
                    audio_concat,
                    silence_path
                ],
                padded_audio
            )

            audio_concat = padded_audio

            audio_duration = get_duration(
                audio_concat
            )

        if cta:

            subtitle_items.append(
                {
                    "text": "",
                    "duration": 0
                }
            )

        muxed = os.path.join(
            work_dir,
            "muxed.mp4"
        )

        mux_audio(
            concat_video,
            audio_concat,
            muxed
        )

        srt = os.path.join(
            work_dir,
            "captions.srt"
        )

        create_srt(
            subtitle_items,
            srt
        )

        final = os.path.join(
            work_dir,
            "final.mp4"
        )

        burn_captions(
            muxed,
            srt,
            final
        )

        if not os.path.exists(
            final
        ):

            raise RuntimeError(
                "Final MP4 missing"
            )

        validate_video(
            final
        )

        size = os.path.getsize(
            final
        )

        log.info(
            "CREATE_REEL_FINAL_SIZE=%s",
            size
        )

        persistent = os.path.join(
            tempfile.gettempdir(),
            "abosaraj_"
            + uuid.uuid4().hex
            + ".mp4"
        )

        shutil.copy2(
            final,
            persistent
        )

        log.info(
            "PHASE=CREATE_REEL_DONE"
        )

        return persistent

    except Exception as error:

        log.error(
            "CREATE_REEL_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        raise

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# HF TEST 3
# =========================================================

def run_hf_test3():

    return check_hf_space()


# =========================================================
# HF TEST 4
# =========================================================

def run_hf_test4():

    client = create_hf_client()

    api = get_hf_api_dict(
        client
    )

    endpoint = get_generate_endpoint(
        api
    )

    params = endpoint.get(
        "parameters",
        []
    )

    return params


# =========================================================
# HF TEST 5
# =========================================================

def run_hf_test():

    test_dir = tempfile.mkdtemp(
        prefix="hf_test_"
    )

    prompt = (
        "A cinematic realistic robot standing alone "
        "in a dark futuristic laboratory, slowly looking "
        "toward the camera, subtle natural body movement, "
        "dramatic cinematic lighting, photorealistic "
        "moving video"
    )

    try:

        video = generate_ai_video(
            prompt,
            test_dir,
            test_mode=True
        )

        info = validate_video(
            video
        )

        duration = info[
            "duration"
        ]

        size = info[
            "size"
        ]

        persistent = os.path.join(
            tempfile.gettempdir(),
            "hf_test_"
            + uuid.uuid4().hex
            + ".mp4"
        )

        shutil.copy2(
            video,
            persistent
        )

        return (
            persistent,
            duration,
            size
        )

    finally:

        shutil.rmtree(
            test_dir,
            ignore_errors=True
        )


# =========================================================
# UPDATE DEDUPLICATION
# =========================================================

def is_duplicate_update(
    update
):

    update_id = update.get(
        "update_id"
    )

    if update_id is None:

        return False

    if update_id in PROCESSED_UPDATES:

        log.warning(
            "DUPLICATE_UPDATE=%s",
            update_id
        )

        return True

    PROCESSED_UPDATES.add(
        update_id
    )

    if len(
        PROCESSED_UPDATES
    ) > MAX_PROCESSED_UPDATES:

        PROCESSED_UPDATES.clear()

        PROCESSED_UPDATES.add(
            update_id
        )

    return False


# =========================================================
# TELEGRAM PROCESSOR
# =========================================================

def process_message(
    update
):

    if not update:

        return

    if is_duplicate_update(
        update
    ):

        return

    message = update.get(
        "message"
    )

    if not message:

        return

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    ).strip()

    if not chat_id or not text:

        return

    log.info(
        "TELEGRAM_MESSAGE chat_id=%s text=%s",
        chat_id,
        text
    )

    # =====================================================
    # START
    # =====================================================

    if text == "/start":

        send_message(
            chat_id,
            "👋 أهلاً بك.\n\n"
            "أرسل فكرة فيديو وسأحولها "
            "إلى فيديو قصير سينمائي.\n\n"
            "أوامر الاختبار:\n"
            "/hftest3 — فحص آمن\n"
            "/hftest4 — قراءة باراميترات Wan\n"
            "/hftest5 — اختبار GPU اقتصادي"
        )

        return

    # =====================================================
    # HF TEST 3
    # =====================================================

    if text == "/hftest3":

        send_message(
            chat_id,
            "🔎 أفحص Hugging Face بدون تشغيل Wan..."
        )

        try:

            result = run_hf_test3()

            if result["ok"]:

                send_message(
                    chat_id,
                    "✅ Hugging Face يعمل.\n\n"
                    f"عدد باراميترات /generate: "
                    f"{result['parameter_count']}\n\n"
                    "ℹ️ هذا الفحص لا يشغل GPU."
                )

            else:

                send_message(
                    chat_id,
                    "❌ فحص Hugging Face فشل:\n\n"
                    + result["error"]
                )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HF TEST 3 فشل:\n\n"
                + safe_error_text(error)
            )

        return

    # =====================================================
    # HF TEST 4
    # =====================================================

    if text == "/hftest4":

        send_message(
            chat_id,
            "🔎 أقرأ باراميترات Wan..."
        )

        try:

            params = run_hf_test4()

            lines = []

            for index, p in enumerate(
                params,
                start=1
            ):

                if not isinstance(
                    p,
                    dict
                ):

                    lines.append(
                        f"{index}. {p}"
                    )

                    continue

                lines.append(
                    f"{index}. "
                    f"name={p.get('name')} "
                    f"default={p.get('default')} "
                    f"type={p.get('type')} "
                    f"choices={p.get('choices')}"
                )

            message_text = (
                "✅ Generate parameters:\n\n"
                + "\n".join(lines)
            )

            if len(
                message_text
            ) > 3900:

                message_text = (
                    message_text[:3900]
                    + "\n\n..."
                )

            send_message(
                chat_id,
                message_text
            )

        except Exception as error:

            send_message(
                chat_id,
                "❌ HF TEST 4 فشل:\n\n"
                + safe_error_text(error)
            )

        return

    # =====================================================
    # HF TEST 5
    # =====================================================

    if text == "/hftest5":

        if not GENERATION_LOCK.acquire(
            blocking=False
        ):

            send_message(
                chat_id,
                "⏳ يوجد توليد آخر يعمل حاليًا.\n\n"
                "لن أشغل GPU ثاني حتى ينتهي الأول."
            )

            return

        video = None

        try:

            send_message(
                chat_id,
                "🎥 بدأت اختبار Wan اقتصادي...\n\n"
                "320×320 | 21 frames | 5 steps\n\n"
                "⚠️ هذا الاختبار يشغل GPU فعليًا."
            )

            safe_check = check_hf_space()

            if not safe_check["ok"]:

                send_message(
                    chat_id,
                    "❌ Hugging Face غير جاهز.\n\n"
                    + safe_check["error"]
                )

                return

            video, duration, size = run_hf_test()

            send_message(
                chat_id,
                "✅✅ Wan اشتغل!\n\n"
                f"⏱️ المدة: {duration:.2f} ثانية\n"
                f"📦 الحجم: "
                f"{size / 1024 / 1024:.2f} MB\n\n"
                "🎬 سأرسل فيديو الاختبار."
            )

            result = send_video(
                chat_id,
                video,
                caption="🎥 Wan Test — SUCCESS"
            )

            if not result:

                send_message(
                    chat_id,
                    "⚠️ التوليد نجح لكن Telegram "
                    "لم يؤكد إرسال الفيديو."
                )

        except Exception as error:

            error_text = safe_error_text(
                error
            )

            log.error(
                "HF_TEST_ERROR=%s",
                error_text,
                exc_info=True
            )

            if (
                "HF_QUOTA_EXHAUSTED"
                in error_text
                or
                is_hf_quota_error(
                    error
                )
            ):

                send_message(
                    chat_id,
                    "🛑 ZeroGPU quota غير كافي حاليًا.\n\n"
                    + error_text
                    + "\n\n"
                    "لا تعيد /hftest5 الآن "
                    "حتى تتجدد الحصة."
                )

            else:

                send_message(
                    chat_id,
                    "❌ اختبار Wan فشل.\n\n"
                    + error_text
                )

        finally:

            GENERATION_LOCK.release()

            if (
                video
                and
                os.path.exists(
                    video
                )
            ):

                try:

                    os.remove(
                        video
                    )

                except Exception:

                    pass

        return

    # =====================================================
    # NORMAL VIDEO
    # =====================================================

    if not GENERATION_LOCK.acquire(
        blocking=False
    ):

        send_message(
            chat_id,
            "⏳ يوجد فيديو آخر قيد التوليد حاليًا.\n\n"
            "لن أشغل GPU ثاني حتى ينتهي."
        )

        return

    final_video = None

    try:

        send_message(
            chat_id,
            "🎬 وصلت الفكرة.\n"
            "🧠 بناء القصة والمشاهد..."
        )

        send_message(
            chat_id,
            "🎥 توليد الفيديو...\n"
            "⏳ اصبر شوي."
        )

        final_video = create_reel(
            text
        )

        send_message(
            chat_id,
            "🎙️ تجهيز الصوت...\n"
            "📝 تجهيز الكابشن..."
        )

        result = send_video(
            chat_id,
            final_video,
            caption="🎬 تم إنشاء الفيديو"
        )

        if not result:

            raise RuntimeError(
                "Telegram failed to send video"
            )

        log.info(
            "FINAL_VIDEO_SENT"
        )

    except Exception as error:

        error_text = safe_error_text(
            error
        )

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            error_text,
            exc_info=True
        )

        if (
            "HF_QUOTA_EXHAUSTED"
            in error_text
            or
            is_hf_quota_error(
                error
            )
        ):

            send_message(
                chat_id,
                "🛑 Hugging Face ZeroGPU quota "
                "غير كافي حاليًا.\n\n"
                "أوقفت العملية ولن أعيد التوليد "
                "تلقائيًا حتى لا نستهلك الحصة."
            )

        else:

            send_message(
                chat_id,
                "❌ صار خطأ أثناء صناعة الفيديو.\n\n"
                "افتح Render Logs وشوف آخر "
                "CREATE_REEL_ERROR."
            )

    finally:

        GENERATION_LOCK.release()

        if (
            final_video
            and
            os.path.exists(
                final_video
            )
        ):

            try:

                os.remove(
                    final_video
                )

            except Exception:

                pass


# =========================================================
# BACKGROUND TELEGRAM JOB
# =========================================================

def process_message_background(
    update
):

    try:

        process_message(
            update
        )

    except Exception as error:

        log.error(
            "BACKGROUND_PROCESS_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook",
    methods=["POST"]
)
def telegram_webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if not update:

            return jsonify(
                {
                    "ok": True
                }
            )

        log.info(
            "TELEGRAM_WEBHOOK_UPDATE_RECEIVED"
        )

        thread = threading.Thread(
            target=process_message_background,
            args=(update,),
            daemon=True
        )

        thread.start()

        return jsonify(
            {
                "ok": True
            }
        )

    except Exception as error:

        log.error(
            "WEBHOOK_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

        return jsonify(
            {
                "ok": False
            }
        ), 200


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify(
        {
            "status": "ok",
            "service": "abosaraj",
            "hf_space": HF_SPACE,
            "model": GROQ_MODEL
        }
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify(
        {
            "status": "healthy"
        }
    )


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    public_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if not public_url:

        log.warning(
            "RENDER_EXTERNAL_URL_NOT_FOUND"
        )

        return

    webhook_url = (
        public_url.rstrip("/")
        + "/telegram/webhook"
    )

    log.info(
        "SETTING_WEBHOOK=%s",
        webhook_url
    )

    result = telegram_api(
        "setWebhook",
        payload={
            "url": webhook_url
        }
    )

    log.info(
        "SET_WEBHOOK_RESULT=%s",
        result
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    log.info(
        "=" * 80
    )

    log.info(
        "ABOSARAJ BOT STARTING"
    )

    log.info(
        "HF_SPACE=%s",
        HF_SPACE
    )

    log.info(
        "GROQ_MODEL=%s",
        GROQ_MODEL
    )

    log.info(
        "GEN_WIDTH=%s",
        GEN_WIDTH
    )

    log.info(
        "GEN_HEIGHT=%s",
        GEN_HEIGHT
    )

    log.info(
        "GEN_FRAMES=%s",
        GEN_FRAMES
    )

    log.info(
        "GEN_STEPS=%s",
        GEN_STEPS
    )

    log.info(
        "GEN_GUIDANCE=%s",
        GEN_GUIDANCE
    )

    log.info(
        "TEST_GEN_WIDTH=%s",
        TEST_GEN_WIDTH
    )

    log.info(
        "TEST_GEN_HEIGHT=%s",
        TEST_GEN_HEIGHT
    )

    log.info(
        "TEST_GEN_FRAMES=%s",
        TEST_GEN_FRAMES
    )

    log.info(
        "TEST_GEN_STEPS=%s",
        TEST_GEN_STEPS
    )

    log.info(
        "MAX_VIDEO_RETRIES=%s",
        MAX_VIDEO_RETRIES
    )

    log.info(
        "CAPAFY_TOKEN_PRESENT=%s",
        bool(
            os.getenv(
                "CAPAFY_ACCESS_TOKEN",
                ""
            ).strip()
        )
    )

    log.info(
        "CAPAFY_TEST_KEY_PRESENT=%s",
        bool(
            os.getenv(
                "CAPAFY_TEST_KEY",
                ""
            ).strip()
        )
    )

    log.info(
        "=" * 80
    )

    try:

        setup_webhook()

    except Exception as error:

        log.error(
            "WEBHOOK_SETUP_ERROR=%s",
            safe_error_text(error),
            exc_info=True
        )

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
