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


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY",
    ""
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)


# =========================================================
# CAPAFY / CLONECUT
# =========================================================

CAPAFY_BASE_URL = (
    "https://api.capafy.ai"
)

CAPAFY_ACCESS_TOKEN = os.getenv(
    "CAPAFY_ACCESS_TOKEN",
    ""
).strip()

CAPAFY_INSTANCE_ID = os.getenv(
    "CAPAFY_INSTANCE_ID",
    ""
).strip()

CLONECUT_AGENT_ID = os.getenv(
    "CLONECUT_AGENT_ID",
    "5133292529"
).strip()


# =========================================================
# VIDEO SETTINGS
# =========================================================

SHOT_COUNT = 2
SHOT_DURATION = 5

FINAL_WIDTH = 720
FINAL_HEIGHT = 1280
FINAL_FPS = 16

TTS_VOICE = (
    "ar-SA-HamedNeural"
)

DEFAULT_CTA = (
    "إذا عجبك الفيديو تابعنا للمزيد"
)


# =========================================================
# SAFETY / RETRY
# =========================================================

MAX_CAPAFY_RETRIES = 2

GENERATION_LOCK = threading.Lock()

PROCESSED_UPDATES = set()

MAX_PROCESSED_UPDATES = 1000


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)

log = logging.getLogger(
    "abosaraj"
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# ERROR HELPERS
# =========================================================

def safe_error_text(error):

    text = str(error)

    secrets = [
        BOT_TOKEN,
        GROQ_API_KEY,
        CAPAFY_ACCESS_TOKEN,
        os.getenv(
            "CAPAFY_API_KEY",
            ""
        ).strip()
    ]

    for secret in secrets:

        if secret:

            text = text.replace(
                secret,
                "***"
            )

    return text


def ensure_dir(path):

    os.makedirs(
        path,
        exist_ok=True
    )

    return path


# =========================================================
# CAPAFY HTTP
# =========================================================

def capafy_request(
    method,
    endpoint,
    params=None,
    payload=None,
    timeout=60
):

    token = os.getenv(
        "CAPAFY_ACCESS_TOKEN",
        ""
    ).strip()

    if not token:

        raise RuntimeError(
            "CAPAFY_ACCESS_TOKEN is missing"
        )

    url = (
        CAPAFY_BASE_URL.rstrip("/")
        + "/"
        + endpoint.lstrip("/")
    )

    headers = {
        "Authorization": (
            f"Bearer {token}"
        ),
        "Accept": "application/json"
    }

    if payload is not None:

        headers[
            "Content-Type"
        ] = "application/json"

    log.info(
        "CAPAFY_REQUEST method=%s endpoint=%s",
        method,
        endpoint
    )

    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=payload,
        timeout=timeout
    )

    log.info(
        "CAPAFY_RESPONSE endpoint=%s status=%s",
        endpoint,
        response.status_code
    )

    try:

        data = response.json()

    except Exception:

        data = response.text[:10000]

    if response.status_code >= 400:

        raise RuntimeError(
            "Capafy HTTP "
            f"{response.status_code}: "
            f"{str(data)[:4000]}"
        )

    return data


# =========================================================
# CAPAFY INSTANCE
# =========================================================

def get_active_instances():

    data = capafy_request(
        "GET",
        "/agent/instance",
        params={
            "status": "active"
        },
        timeout=60
    )

    if not isinstance(
        data,
        dict
    ):

        return []

    body = data.get(
        "data"
    )

    if not isinstance(
        body,
        dict
    ):

        return []

    instances = body.get(
        "instances"
    )

    if not isinstance(
        instances,
        list
    ):

        return []

    return instances


def find_clonecut_instance():

    configured = os.getenv(
        "CAPAFY_INSTANCE_ID",
        ""
    ).strip()

    if configured:

        log.info(
            "USING_CONFIGURED_CAPAFY_INSTANCE=%s",
            configured
        )

        return configured

    instances = (
        get_active_instances()
    )

    for instance in instances:

        if not isinstance(
            instance,
            dict
        ):

            continue

        agent_id = str(
            instance.get(
                "agentId",
                ""
            )
        )

        instance_id = str(
            instance.get(
                "instanceId",
                ""
            )
        )

        if (
            agent_id
            == CLONECUT_AGENT_ID
            and instance_id
        ):

            log.info(
                "CLONECUT_ACTIVE_INSTANCE_FOUND=%s",
                instance_id
            )

            return instance_id

    return None


# =========================================================
# CAPAFY MESSAGE HISTORY
# =========================================================

def get_instance_messages(
    instance_id
):

    return capafy_request(
        "GET",
        (
            "/agent/relay/instances/"
            f"{instance_id}/messages"
        ),
        timeout=60
    )


# =========================================================
# CAPAFY INTERRUPT
# =========================================================

def interrupt_instance(
    instance_id
):

    return capafy_request(
        "POST",
        (
            "/agent/relay/instances/"
            f"{instance_id}/interrupt"
        ),
        timeout=60
    )


# =========================================================
# EXTRACT URLS FROM ANY OBJECT
# =========================================================

def extract_urls(
    value
):

    found = []

    if value is None:

        return found

    if isinstance(
        value,
        str
    ):

        matches = re.findall(
            r"https?://[^\s\"'<>]+",
            value
        )

        for item in matches:

            cleaned = item.rstrip(
                ".,);]}"
            )

            if cleaned not in found:

                found.append(
                    cleaned
                )

        return found

    if isinstance(
        value,
        dict
    ):

        for key, item in value.items():

            key_lower = str(
                key
            ).lower()

            if (
                "url"
                in key_lower
                or
                "file"
                in key_lower
                or
                "video"
                in key_lower
                or
                "download"
                in key_lower
                or
                "output"
                in key_lower
            ):

                found.extend(
                    extract_urls(
                        item
                    )
                )

            else:

                found.extend(
                    extract_urls(
                        item
                    )
                )

        return list(
            dict.fromkeys(
                found
            )
        )

    if isinstance(
        value,
        (list, tuple)
    ):

        for item in value:

            found.extend(
                extract_urls(
                    item
                )
            )

        return list(
            dict.fromkeys(
                found
            )
        )

    return found


# =========================================================
# CAPAFY SSE PARSER
# =========================================================

def parse_sse_event(
    raw_event
):

    raw_event = raw_event.strip()

    if not raw_event:

        return None

    event_name = "message"

    data_lines = []

    for line in raw_event.splitlines():

        line = line.strip(
            "\r"
        )

        if line.startswith(
            "event:"
        ):

            event_name = (
                line[
                    len("event:")
                :].strip()
            )

        elif line.startswith(
            "data:"
        ):

            data_lines.append(
                line[
                    len("data:")
                :].lstrip()
            )

    if not data_lines:

        return {
            "event": event_name,
            "data": raw_event
        }

    data_text = "\n".join(
        data_lines
    )

    try:

        data = json.loads(
            data_text
        )

    except Exception:

        data = data_text

    return {
        "event": event_name,
        "data": data
    }


# =========================================================
# CAPAFY SSE CHAT
# =========================================================

def capafy_chat(
    instance_id,
    content,
    original_question=None,
    next_step_plan=None,
    files=None,
    timeout=900
):

    if not instance_id:

        raise RuntimeError(
            "CAPAFY_INSTANCE_ID is missing"
        )

    token = os.getenv(
        "CAPAFY_ACCESS_TOKEN",
        ""
    ).strip()

    if not token:

        raise RuntimeError(
            "CAPAFY_ACCESS_TOKEN is missing"
        )

    url = (
        CAPAFY_BASE_URL.rstrip("/")
        + "/agent/relay/instances/"
        + instance_id
        + "/messages"
    )

    payload = {
        "content": str(
            content
        )
    }

    if original_question:

        payload[
            "originalQuestion"
        ] = str(
            original_question
        )

    if next_step_plan:

        payload[
            "nextStepPlan"
        ] = str(
            next_step_plan
        )

    if files:

        payload[
            "files"
        ] = files

    headers = {
        "Authorization": (
            f"Bearer {token}"
        ),
        "Accept": (
            "text/event-stream, "
            "application/json"
        ),
        "Content-Type": (
            "application/json"
        ),
        "Cache-Control": "no-cache"
    }

    log.info(
        "CAPAFY_CHAT_START instance=%s",
        instance_id
    )

    try:

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(
                30,
                timeout
            )
        )

    except Exception as error:

        raise RuntimeError(
            "Capafy connection failed: "
            + safe_error_text(error)
        )

    log.info(
        "CAPAFY_CHAT_STATUS=%s",
        response.status_code
    )

    if response.status_code == 409:

        response.close()

        raise RuntimeError(
            "CAPAFY_INSTANCE_BUSY"
        )

    if response.status_code >= 400:

        try:

            error_body = (
                response.text[:5000]
            )

        except Exception:

            error_body = ""

        response.close()

        raise RuntimeError(
            "Capafy chat HTTP "
            f"{response.status_code}: "
            f"{error_body}"
        )

    event_buffer = []

    reply_parts = []

    all_urls = []

    last_event_time = time.time()

    completed = False

    try:

        for raw_line in response.iter_lines(
            decode_unicode=True
        ):

            now = time.time()

            if (
                now - last_event_time
                > timeout
            ):

                raise RuntimeError(
                    "CAPAFY_SSE_TIMEOUT"
                )

            if raw_line is None:

                continue

            line = str(
                raw_line
            )

            if line == "":

                if event_buffer:

                    event = (
                        parse_sse_event(
                            "\n".join(
                                event_buffer
                            )
                        )
                    )

                    event_buffer = []

                    if event:

                        event_data = event.get(
                            "data"
                        )

                        event_name = str(
                            event.get(
                                "event",
                                ""
                            )
                        ).lower()

                        log.info(
                            "CAPAFY_SSE_EVENT=%s",
                            event_name
                        )

                        urls = extract_urls(
                            event_data
                        )

                        all_urls.extend(
                            urls
                        )

                        if (
                            event_name
                            in [
                                "reply",
                                "message",
                                "result"
                            ]
                        ):

                            if isinstance(
                                event_data,
                                str
                            ):

                                reply_parts.append(
                                    event_data
                                )

                            elif isinstance(
                                event_data,
                                dict
                            ):

                                for key in [
                                    "reply",
                                    "content",
                                    "message",
                                    "text",
                                    "answer"
                                ]:

                                    value = (
                                        event_data.get(
                                            key
                                        )
                                    )

                                    if isinstance(
                                        value,
                                        str
                                    ) and value:

                                        reply_parts.append(
                                            value
                                        )

                        if event_name in [
                            "reply",
                            "completed",
                            "complete",
                            "done"
                        ]:

                            completed = True

                        if event_name == "interrupted":

                            raise RuntimeError(
                                "CAPAFY_TASK_INTERRUPTED"
                            )

                        if event_name == "timeout":

                            completed = True

                last_event_time = now

                continue

            event_buffer.append(
                line
            )

            last_event_time = now

        if event_buffer:

            event = parse_sse_event(
                "\n".join(
                    event_buffer
                )
            )

            if event:

                event_data = event.get(
                    "data"
                )

                all_urls.extend(
                    extract_urls(
                        event_data
                    )
                )

                if isinstance(
                    event_data,
                    str
                ):

                    reply_parts.append(
                        event_data
                    )

                elif isinstance(
                    event_data,
                    dict
                ):

                    for key in [
                        "reply",
                        "content",
                        "message",
                        "text",
                        "answer"
                    ]:

                        value = (
                            event_data.get(
                                key
                            )
                        )

                        if isinstance(
                            value,
                            str
                        ) and value:

                            reply_parts.append(
                                value
                            )

    finally:

        response.close()

    all_urls = list(
        dict.fromkeys(
            all_urls
        )
    )

    reply_text = "\n".join(
        x.strip()
        for x in reply_parts
        if str(x).strip()
    ).strip()

    log.info(
        "CAPAFY_CHAT_DONE urls=%s reply_chars=%s",
        len(all_urls),
        len(reply_text)
    )

    return {
        "completed": completed,
        "reply": reply_text,
        "urls": all_urls
    }


# =========================================================
# DOWNLOAD
# =========================================================

def download_url(
    url,
    destination
):

    log.info(
        "DOWNLOAD_START url=%s",
        url[:300]
    )

    with requests.get(
        url,
        stream=True,
        timeout=600
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
        "DOWNLOAD_DONE size=%s",
        size
    )

    return destination


# =========================================================
# VIDEO VALIDATION
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

        raise RuntimeError(
            "Command timed out"
        )

    except Exception as error:

        raise RuntimeError(
            safe_error_text(error)
        )

    if result.stdout:

        log.info(
            "COMMAND_STDOUT=%s",
            result.stdout[-3000:]
        )

    if result.stderr:

        log.info(
            "COMMAND_STDERR=%s",
            result.stderr[-3000:]
        )

    if result.returncode != 0:

        raise RuntimeError(
            "Command failed with code "
            f"{result.returncode}: "
            f"{result.stderr[-3000:]}"
        )

    return result


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

    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            (
                "default="
                "noprint_wrappers=1:"
                "nokey=1"
            ),
            path
        ],
        timeout=60
    )

    try:

        duration = float(
            result.stdout.strip()
        )

    except Exception:

        raise RuntimeError(
            "Could not read video duration"
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
# GROQ JSON
# =========================================================

def extract_json_object(
    raw
):

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
            "Could not find JSON object"
        )

    return json.loads(
        raw[
            start:end + 1
        ]
    )


# =========================================================
# GROQ STORYBOARD
# =========================================================

def create_storyboard(
    user_idea
):

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY is missing"
        )

    client = Groq(
        api_key=GROQ_API_KEY
    )

    prompt = f"""
أنت كاتب ومخرج محتوى أطفال محترف.

حوّل فكرة المستخدم إلى فيديو قصير
عمودي مناسب للأطفال من عمر 4 إلى 8 سنوات.

فكرة المستخدم:
{user_idea}

نريد شخصيات ثابتة بين المشاهد.
اجعل القصة بسيطة، ممتعة، آمنة، واضحة بصريًا،
وبها بداية قوية جدًا.

أخرج JSON فقط بهذا الشكل:

{{
  "title": "عنوان قصير",
  "hook": "Hook",
  "narration": [
    "تعليق صوتي للمشهد الأول",
    "تعليق صوتي للمشهد الثاني"
  ],
  "cta": "دعوة قصيرة",
  "scenes": [
    {{
      "scene": 1,
      "prompt": "English cinematic video prompt",
      "duration": 5
    }},
    {{
      "scene": 2,
      "prompt": "English cinematic video prompt",
      "duration": 5
    }}
  ]
}}

القواعد:

- عدد المشاهد: {SHOT_COUNT}
- مدة كل مشهد: {SHOT_DURATION} ثوانٍ تقريبًا.
- prompts باللغة الإنجليزية.
- الشخصيات يجب أن تبقى متناسقة.
- نفس الملابس والألوان والشكل بين المشاهد.
- أسلوب بصري سينمائي عالي الجودة.
- مناسب للأطفال.
- لا عنف دموي.
- لا رعب.
- لا محتوى جنسي.
- لا شعارات.
- لا Watermarks.
- لا نصوص مكتوبة داخل الفيديو.
- أول مشهد Hook بصري قوي.
- آخر مشهد فيه نهاية لطيفة أو Hook للحلقة القادمة.
- لا تكتب أي شيء خارج JSON.
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert children's "
                    "short-form video director."
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
        "GROQ_RESPONSE=%s",
        raw[:8000]
    )

    data = extract_json_object(
        raw
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "Storyboard is not an object"
        )

    scenes = data.get(
        "scenes"
    )

    if not isinstance(
        scenes,
        list
    ):

        raise RuntimeError(
            "Storyboard scenes missing"
        )

    clean_scenes = []

    for index, scene in enumerate(
        scenes[
            :SHOT_COUNT
        ],
        start=1
    ):

        if not isinstance(
            scene,
            dict
        ):

            continue

        prompt = str(
            scene.get(
                "prompt",
                ""
            )
        ).strip()

        if not prompt:

            continue

        clean_scenes.append(
            {
                "scene": index,
                "prompt": prompt,
                "duration": SHOT_DURATION
            }
        )

    if not clean_scenes:

        raise RuntimeError(
            "No valid scenes"
        )

    data[
        "scenes"
    ] = clean_scenes

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
        str(
            item
        ).strip()
        for item in narration
    ]

    while len(
        narration
    ) < len(
        clean_scenes
    ):

        narration.append(
            ""
        )

    data[
        "narration"
    ] = narration[
        :len(
            clean_scenes
        )
    ]

    cta = str(
        data.get(
            "cta",
            DEFAULT_CTA
        )
    ).strip()

    data[
        "cta"
    ] = (
        cta
        or
        DEFAULT_CTA
    )

    return data


# =========================================================
# CLONECUT PROMPT
# =========================================================

def build_clonecut_prompt(
    storyboard
):

    title = str(
        storyboard.get(
            "title",
            ""
        )
    ).strip()

    hook = str(
        storyboard.get(
            "hook",
            ""
        )
    ).strip()

    scenes = storyboard.get(
        "scenes",
        []
    )

    lines = []

    lines.append(
        "Create a vertical short-form video."
    )

    lines.append(
        "Target platform: TikTok / Reels / Shorts."
    )

    lines.append(
        "Aspect ratio: 9:16."
    )

    lines.append(
        "Target audience: children ages 4-8."
    )

    lines.append(
        "Keep the characters visually consistent."
    )

    lines.append(
        "No logos, no watermark, no written text."
    )

    lines.append(
        "Safe, colorful, family-friendly content."
    )

    if title:

        lines.append(
            f"TITLE: {title}"
        )

    if hook:

        lines.append(
            f"HOOK: {hook}"
        )

    lines.append(
        ""
    )

    lines.append(
        "SCENES:"
    )

    for index, scene in enumerate(
        scenes,
        start=1
    ):

        prompt = str(
            scene.get(
                "prompt",
                ""
            )
        ).strip()

        lines.append(
            f"Scene {index} "
            f"({SHOT_DURATION} seconds):"
        )

        lines.append(
            prompt
        )

        lines.append(
            ""
        )

    lines.append(
        "Generate the finished video output."
    )

    lines.append(
        "Return the generated video file."
    )

    return "\n".join(
        lines
    )


# =========================================================
# CLONECUT GENERATION
# =========================================================

def generate_with_clonecut(
    storyboard,
    output_dir
):

    instance_id = (
        find_clonecut_instance()
    )

    if not instance_id:

        raise RuntimeError(
            "NO_CLONECUT_INSTANCE\n"
            "CloneCut is not active yet. "
            "Purchase/activate CloneCut first, "
            "then add CAPAFY_INSTANCE_ID to Render."
        )

    prompt = build_clonecut_prompt(
        storyboard
    )

    log.info(
        "CLONECUT_PROMPT=%s",
        prompt[:10000]
    )

    last_error = None

    for attempt in range(
        1,
        MAX_CAPAFY_RETRIES + 1
    ):

        try:

            result = capafy_chat(
                instance_id=instance_id,
                content=prompt,
                original_question=(
                    "Create a short children's "
                    "vertical video from this idea."
                ),
                next_step_plan=(
                    "Return the generated video "
                    "file when finished."
                ),
                timeout=900
            )

            urls = result.get(
                "urls",
                []
            )

            reply = result.get(
                "reply",
                ""
            )

            log.info(
                "CLONECUT_REPLY=%s",
                reply[:5000]
            )

            if not urls:

                # Sometimes the SSE response
                # contains the result metadata
                # but the final file appears
                # in message history.
                try:

                    history = (
                        get_instance_messages(
                            instance_id
                        )
                    )

                    history_urls = (
                        extract_urls(
                            history
                        )
                    )

                    urls.extend(
                        history_urls
                    )

                except Exception as history_error:

                    log.warning(
                        "HISTORY_CHECK_FAILED=%s",
                        safe_error_text(
                            history_error
                        )
                    )

            urls = list(
                dict.fromkeys(
                    urls
                )
            )

            if not urls:

                raise RuntimeError(
                    "CloneCut finished without "
                    "returning a video URL.\n"
                    + reply[:2000]
                )

            # Prefer video-looking URLs.
            video_urls = []

            for url in urls:

                lower = url.lower()

                if any(
                    ext in lower
                    for ext in [
                        ".mp4",
                        ".mov",
                        ".webm",
                        ".m4v"
                    ]
                ):

                    video_urls.append(
                        url
                    )

            selected_url = (
                video_urls[0]
                if video_urls
                else urls[0]
            )

            destination = os.path.join(
                output_dir,
                "clonecut_video.mp4"
            )

            download_url(
                selected_url,
                destination
            )

            validate_video(
                destination
            )

            log.info(
                "CLONECUT_VIDEO_READY=%s",
                destination
            )

            return destination

        except Exception as error:

            last_error = error

            error_text = (
                safe_error_text(
                    error
                )
            )

            log.error(
                "CLONECUT_ATTEMPT_%s_ERROR=%s",
                attempt,
                error_text,
                exc_info=True
            )

            if (
                "CAPAFY_INSTANCE_BUSY"
                in error_text
            ):

                raise

            if attempt < MAX_CAPAFY_RETRIES:

                time.sleep(
                    attempt * 3
                )

    raise RuntimeError(
        "CLONECUT_GENERATION_FAILED: "
        + safe_error_text(
            last_error
        )
    )


# =========================================================
# NORMALIZE VIDEO
# =========================================================

def normalize_video(
    input_path,
    output_path
):

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
        timeout=900
    )

    validate_video(
        output_path
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

    asyncio.run(
        _tts(
            text,
            output_path
        )
    )

    if not os.path.exists(
        output_path
    ):

        raise RuntimeError(
            "TTS file was not created"
        )

    if os.path.getsize(
        output_path
    ) < 100:

        raise RuntimeError(
            "TTS file is empty"
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
            (
                "default="
                "noprint_wrappers=1:"
                "nokey=1"
            ),
            path
        ],
        timeout=60
    )

    return float(
        result.stdout.strip()
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
    ) as file:

        for path in audio_paths:

            absolute = os.path.abspath(
                path
            )

            escaped = absolute.replace(
                "'",
                "'\\''"
            )

            file.write(
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
# SILENCE
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
# MUX AUDIO
# =========================================================

def mux_audio(
    video_path,
    audio_path,
    output_path
):

    video_duration = (
        get_duration(
            video_path
        )
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

    return output_path


# =========================================================
# SRT
# =========================================================

def format_srt_time(
    seconds
):

    total_ms = int(
        round(
            float(seconds)
            * 1000
        )
    )

    hours = (
        total_ms
        // 3600000
    )

    minutes = (
        total_ms
        % 3600000
    ) // 60000

    secs = (
        total_ms
        % 60000
    ) // 1000

    milliseconds = (
        total_ms
        % 1000
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
    ) as file:

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

                file.write(
                    f"{subtitle_index}\n"
                )

                file.write(
                    f"{format_srt_time(start)}"
                    " --> "
                    f"{format_srt_time(end)}\n"
                )

                file.write(
                    text
                    + "\n\n"
                )

                subtitle_index += 1

            current = end

    return output_path


# =========================================================
# BURN CAPTIONS
# =========================================================

def burn_captions(
    video_path,
    srt_path,
    output_path
):

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
        "CREATE_REEL_START work_dir=%s",
        work_dir
    )

    try:

        # -------------------------------------------------
        # 1. STORY
        # -------------------------------------------------

        storyboard = (
            create_storyboard(
                user_idea
            )
        )

        # -------------------------------------------------
        # 2. CLONECUT
        # -------------------------------------------------

        raw_dir = ensure_dir(
            os.path.join(
                work_dir,
                "raw"
            )
        )

        raw_video = (
            generate_with_clonecut(
                storyboard,
                raw_dir
            )
        )

        # -------------------------------------------------
        # 3. NORMALIZE
        # -------------------------------------------------

        normalized = os.path.join(
            work_dir,
            "normalized.mp4"
        )

        normalize_video(
            raw_video,
            normalized
        )

        # -------------------------------------------------
        # 4. TTS
        # -------------------------------------------------

        audio_dir = ensure_dir(
            os.path.join(
                work_dir,
                "audio"
            )
        )

        narration = (
            storyboard.get(
                "narration",
                []
            )
        )

        audio_files = []

        for index, text in enumerate(
            narration,
            start=1
        ):

            text = str(
                text
            ).strip()

            if not text:

                text = " "

            audio_path = os.path.join(
                audio_dir,
                f"voice_{index}.mp3"
            )

            generate_tts(
                text,
                audio_path
            )

            audio_files.append(
                audio_path
            )

        if not audio_files:

            audio_path = os.path.join(
                audio_dir,
                "voice.mp3"
            )

            generate_tts(
                " ",
                audio_path
            )

            audio_files.append(
                audio_path
            )

        audio_concat = os.path.join(
            work_dir,
            "audio.m4a"
        )

        concat_audio(
            audio_files,
            audio_concat
        )

        # -------------------------------------------------
        # 5. FIT AUDIO
        # -------------------------------------------------

        video_duration = (
            get_duration(
                normalized
            )
        )

        audio_duration = (
            get_duration(
                audio_concat
            )
        )

        if audio_duration < video_duration:

            silence_path = os.path.join(
                audio_dir,
                "silence.m4a"
            )

            create_silence(
                video_duration
                - audio_duration,
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

            audio_concat = (
                padded_audio
            )

        # -------------------------------------------------
        # 6. MUX
        # -------------------------------------------------

        muxed = os.path.join(
            work_dir,
            "muxed.mp4"
        )

        mux_audio(
            normalized,
            audio_concat,
            muxed
        )

        # -------------------------------------------------
        # 7. CAPTIONS
        # -------------------------------------------------

        subtitle_items = []

        if isinstance(
            narration,
            list
        ):

            scene_count = len(
                storyboard.get(
                    "scenes",
                    []
                )
            )

            per_scene_duration = (
                video_duration
                / max(
                    1,
                    scene_count
                )
            )

            for text in narration:

                text = str(
                    text
                ).strip()

                if text:

                    subtitle_items.append(
                        {
                            "text": text,
                            "duration": (
                                per_scene_duration
                            )
                        }
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

        if subtitle_items:

            burn_captions(
                muxed,
                srt,
                final
            )

        else:

            shutil.copy2(
                muxed,
                final
            )

        validate_video(
            final
        )

        # -------------------------------------------------
        # 8. PERSIST
        # -------------------------------------------------

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
            "CREATE_REEL_DONE path=%s",
            persistent
        )

        return persistent

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# TELEGRAM
# =========================================================

def telegram_api(
    method,
    payload=None,
    files=None
):

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing"
        )

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        data=payload,
        files=files,
        timeout=300
    )

    if not response.ok:

        raise RuntimeError(
            "Telegram API "
            f"{response.status_code}: "
            f"{response.text[:3000]}"
        )

    try:

        return response.json()

    except Exception:

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

    payload = {
        "chat_id": chat_id
    }

    if caption:

        payload[
            "caption"
        ] = caption

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

    text = str(
        message.get(
            "text",
            ""
        )
    ).strip()

    if not chat_id or not text:

        return

    log.info(
        "TELEGRAM_MESSAGE chat=%s text=%s",
        chat_id,
        text
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        instance_id = (
            find_clonecut_instance()
        )

        if instance_id:

            status = (
                "🟢 CloneCut مربوط وجاهز."
            )

        else:

            status = (
                "🟡 CloneCut لم يتم تفعيل Instance "
                "له بعد."
            )

        send_message(
            chat_id,
            "👋 أهلاً بك.\n\n"
            "أرسل فكرة الفيديو وأنا أحولها "
            "لفيديو قصير.\n\n"
            f"{status}\n\n"
            "المحرك الحالي: CloneCut / Seedance 2.0"
        )

        return

    # -----------------------------------------------------
    # CAPAFY STATUS
    # -----------------------------------------------------

    if text == "/clonecut":

        try:

            instance_id = (
                find_clonecut_instance()
            )

            if instance_id:

                send_message(
                    chat_id,
                    "🟢 CloneCut جاهز.\n\n"
                    f"Instance: {instance_id}"
                )

            else:

                send_message(
                    chat_id,
                    "🟡 ما في CloneCut Instance "
                    "فعالة حاليًا.\n\n"
                    "فعّل CloneCut وبعدها أضف "
                    "CAPAFY_INSTANCE_ID في Render."
                )

        except Exception as error:

            send_message(
                chat_id,
                "❌ فحص CloneCut فشل:\n\n"
                + safe_error_text(error)
            )

        return

    # -----------------------------------------------------
    # NORMAL VIDEO
    # -----------------------------------------------------

    if not GENERATION_LOCK.acquire(
        blocking=False
    ):

        send_message(
            chat_id,
            "⏳ في فيديو آخر قيد التوليد حاليًا.\n\n"
            "استنى يخلص الأول."
        )

        return

    final_video = None

    try:

        instance_id = (
            find_clonecut_instance()
        )

        if not instance_id:

            send_message(
                chat_id,
                "🟡 البوت جاهز، لكن CloneCut "
                "لسه ما إله Instance فعالة.\n\n"
                "فعّل CloneCut أولًا، وبعدها "
                "حط CAPAFY_INSTANCE_ID في Render."
            )

            return

        send_message(
            chat_id,
            "🎬 وصلت الفكرة.\n\n"
            "🧠 أبني القصة والمشاهد..."
        )

        send_message(
            chat_id,
            "🎥 أرسل الآن المهمة إلى "
            "CloneCut / Seedance 2.0...\n\n"
            "⏳ التوليد ممكن يأخذ عدة دقائق."
        )

        final_video = create_reel(
            text
        )

        send_message(
            chat_id,
            "🎙️ الفيديو جاهز.\n"
            "📝 أجهز النسخة النهائية..."
        )

        result = send_video(
            chat_id,
            final_video,
            caption=(
                "🎬 تم إنشاء الفيديو "
                "بواسطة CloneCut"
            )
        )

        if not result:

            raise RuntimeError(
                "Telegram failed to send video"
            )

        send_message(
            chat_id,
            "✅ خلص الفيديو ووصلك."
        )

    except Exception as error:

        error_text = (
            safe_error_text(
                error
            )
        )

        log.error(
            "TELEGRAM_HANDLER_ERROR=%s",
            error_text,
            exc_info=True
        )

        if (
            "CAPAFY_INSTANCE_BUSY"
            in error_text
        ):

            send_message(
                chat_id,
                "⏳ CloneCut مشغول حاليًا "
                "بمهمة ثانية.\n\n"
                "استنى المهمة الحالية تخلص "
                "وبعدين جرّب مرة ثانية."
            )

        elif (
            "NO_CLONECUT_INSTANCE"
            in error_text
        ):

            send_message(
                chat_id,
                "🟡 CloneCut غير مفعّل بعد.\n\n"
                "لازم نفعّل الـInstance أولًا "
                "وبعدين نضع Instance ID في Render."
            )

        else:

            send_message(
                chat_id,
                "❌ صار خطأ أثناء إنشاء الفيديو.\n\n"
                "آخر خطأ:\n"
                + error_text[:2500]
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
# BACKGROUND
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
            "BACKGROUND_ERROR=%s",
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

        thread = threading.Thread(
            target=(
                process_message_background
            ),
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
            "engine": "capafy-clonecut",
            "clonecut_agent_id": (
                CLONECUT_AGENT_ID
            ),
            "clonecut_instance_configured": bool(
                os.getenv(
                    "CAPAFY_INSTANCE_ID",
                    ""
                ).strip()
            ),
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
# CLONECUT HEALTH CHECK
# =========================================================

@app.route(
    "/clonecut-status",
    methods=["GET"]
)
def clonecut_status():

    try:

        instances = (
            get_active_instances()
        )

        clonecut_instances = []

        for instance in instances:

            if not isinstance(
                instance,
                dict
            ):

                continue

            if str(
                instance.get(
                    "agentId",
                    ""
                )
            ) == CLONECUT_AGENT_ID:

                clonecut_instances.append(
                    {
                        "instanceId": instance.get(
                            "instanceId"
                        ),
                        "agentId": instance.get(
                            "agentId"
                        ),
                        "status": instance.get(
                            "status"
                        ),
                        "agentTitle": instance.get(
                            "agentTitle"
                        ),
                        "createdAt": instance.get(
                            "createdAt"
                        ),
                        "expiresAt": instance.get(
                            "expiresAt"
                        )
                    }
                )

        return jsonify(
            {
                "ok": True,
                "engine": "CloneCut",
                "agentId": CLONECUT_AGENT_ID,
                "configuredInstance": bool(
                    os.getenv(
                        "CAPAFY_INSTANCE_ID",
                        ""
                    ).strip()
                ),
                "activeCloneCutInstances": (
                    clonecut_instances
                )
            }
        )

    except Exception as error:

        return jsonify(
            {
                "ok": False,
                "error": safe_error_text(
                    error
                )
            }
        ), 500


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
        "ENGINE=CAPAFY_CLONECUT"
    )

    log.info(
        "CLONECUT_AGENT_ID=%s",
        CLONECUT_AGENT_ID
    )

    log.info(
        "CLONECUT_INSTANCE_PRESENT=%s",
        bool(
            os.getenv(
                "CAPAFY_INSTANCE_ID",
                ""
            ).strip()
        )
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
        "GROQ_MODEL=%s",
        GROQ_MODEL
    )

    log.info(
        "FINAL_VIDEO=%sx%s",
        FINAL_WIDTH,
        FINAL_HEIGHT
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
