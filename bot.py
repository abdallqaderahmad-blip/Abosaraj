# =========================================================
# CAPAFY SAFE TEST
# =========================================================

@app.route(
    "/capafy-test",
    methods=["GET"]
)
def capafy_test():

    import hashlib

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
            "error": "Unauthorized",
            "debug": {
                "supplied_present": bool(
                    supplied_key
                ),
                "stored_present": bool(
                    debug_key
                ),
                "supplied_length": len(
                    supplied_key
                ),
                "stored_length": len(
                    debug_key
                ),
                "supplied_hash": hashlib.sha256(
                    supplied_key.encode()
                ).hexdigest()[:12],
                "stored_hash": hashlib.sha256(
                    debug_key.encode()
                ).hexdigest()[:12]
            }
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
