@app.route("/capafy-clonecut-test", methods=["GET"])
def capafy_clonecut_test():
    import hashlib
    import hmac

    # مفتاح الاختبار الموجود في Render
    stored_key = os.getenv("CAPAFY_TEST_KEY", "").strip()
    supplied_key = request.args.get("key", "").strip()

    # تحقق من مفتاح الاختبار
    if not stored_key:
        return jsonify({
            "ok": False,
            "error": "CAPAFY_TEST_KEY is missing"
        }), 500

    if not hmac.compare_digest(supplied_key, stored_key):
        return jsonify({
            "ok": False,
            "error": "Unauthorized",
            "debug": {
                "supplied_present": bool(supplied_key),
                "stored_present": bool(stored_key),
                "supplied_length": len(supplied_key),
                "stored_length": len(stored_key),
                "supplied_hash": hashlib.sha256(
                    supplied_key.encode()
                ).hexdigest()[:12],
                "stored_hash": hashlib.sha256(
                    stored_key.encode()
                ).hexdigest()[:12]
            }
        }), 401

    result = {
        "ok": True,
        "purchase_created": False,
        "credits_spent": False,
        "clonecut": {
            "agent_id": "5133292529",
            "details": None,
            "http_status": None
        },
        "active_instances": [],
        "expired_instances": []
    }

    try:
        # -------------------------------------------------
        # 1) READ-ONLY: جلب تفاصيل CloneCut
        # -------------------------------------------------
        status, data = capafy_request(
            "GET",
            "/agent/agent/agents/5133292529"
        )

        result["clonecut"]["http_status"] = status

        if isinstance(data, dict):
            result["clonecut"]["details"] = data
        else:
            result["clonecut"]["details"] = {
                "raw": str(data)[:4000]
            }

        # -------------------------------------------------
        # 2) READ-ONLY: جلب الـ Active Instances
        # -------------------------------------------------
        status_active, active_data = capafy_request(
            "GET",
            "/agent/instance",
            params={"status": "active"}
        )

        if isinstance(active_data, dict):
            active_list = (
                active_data.get("data", {}).get("instances", [])
                if isinstance(active_data.get("data"), dict)
                else []
            )
        else:
            active_list = []

        result["active_instances"] = active_list

        # -------------------------------------------------
        # 3) READ-ONLY: جلب الـ Expired Instances
        # -------------------------------------------------
        status_expired, expired_data = capafy_request(
            "GET",
            "/agent/instance",
            params={"status": "expired"}
        )

        if isinstance(expired_data, dict):
            expired_list = (
                expired_data.get("data", {}).get("instances", [])
                if isinstance(expired_data.get("data"), dict)
                else []
            )
        else:
            expired_list = []

        result["expired_instances"] = expired_list

        # -------------------------------------------------
        # 4) البحث عن Instance مرتبط بـ CloneCut
        # -------------------------------------------------
        clonecut_matches = []

        for instance in active_list:
            if not isinstance(instance, dict):
                continue

            if str(instance.get("agentId", "")) == "5133292529":
                clonecut_matches.append(instance)

        result["clonecut_matches"] = clonecut_matches
        result["matching_instance"] = (
            clonecut_matches[0]
            if clonecut_matches
            else None
        )

        # -------------------------------------------------
        # ملخص آمن
        # -------------------------------------------------
        result["summary"] = {
            "clonecut_details_http_status": status,
            "active_instances_http_status": status_active,
            "expired_instances_http_status": status_expired,
            "active_instance_count": len(active_list),
            "expired_instance_count": len(expired_list),
            "matching_clonecut_instance_count": len(clonecut_matches),
            "purchase_created": False,
            "credits_spent": False
        }

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": safe_error_text(str(e)),
            "purchase_created": False,
            "credits_spent": False
        }), 500
