async def hf_test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    log.warning("========== HFT_TEST3_START ==========")

    await update.message.reply_text(
        "🧪 HFT_TEST3 بدأ الفحص العميق لـ Hugging Face..."
    )

    try:
        # ==========================================
        # CREATE CLIENT
        # ==========================================
        client = get_hf_client()

        log.warning("HFT_TEST3_CLIENT_CREATED")
        log.warning(
            "HFT_TEST3_CLIENT_TYPE=%s",
            type(client).__name__
        )

        # ==========================================
        # CLIENT DICT
        # ==========================================
        try:
            client_dict = getattr(
                client,
                "__dict__",
                {}
            )

            log.warning(
                "========== HFT_TEST3_CLIENT_DICT =========="
            )

            if isinstance(client_dict, dict):
                for key, value in client_dict.items():
                    try:
                        text_value = repr(value)

                        # Don't dump huge objects
                        if len(text_value) > 3000:
                            text_value = (
                                text_value[:3000]
                                + "...[TRUNCATED]"
                            )

                        log.warning(
                            "CLIENT_ATTR %s = %s",
                            key,
                            text_value
                        )

                    except Exception as attr_error:
                        log.warning(
                            "CLIENT_ATTR_ERROR %s = %s",
                            key,
                            safe_error_text(attr_error)
                        )

            else:
                log.warning(
                    "CLIENT_DICT_TYPE=%s",
                    type(client_dict).__name__
                )

            log.warning(
                "========== HFT_TEST3_CLIENT_DICT_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_CLIENT_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # ==========================================
        # VIEW API
        # ==========================================
        try:
            log.warning(
                "========== HFT_TEST3_VIEW_API_START =========="
            )

            api_result = client.view_api()

            log.warning(
                "HFT_TEST3_VIEW_API_TYPE=%s",
                type(api_result).__name__
            )

            api_repr = repr(api_result)

            if len(api_repr) > 12000:
                api_repr = (
                    api_repr[:12000]
                    + "...[TRUNCATED]"
                )

            log.warning(
                "HFT_TEST3_VIEW_API_RESULT=%s",
                api_repr
            )

            log.warning(
                "========== HFT_TEST3_VIEW_API_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_VIEW_API_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # ==========================================
        # VIEW API AS DICT
        # ==========================================
        try:
            log.warning(
                "========== HFT_TEST3_DICT_START =========="
            )

            api_dict = client.view_api(
                return_format="dict"
            )

            log.warning(
                "HFT_TEST3_DICT_TYPE=%s",
                type(api_dict).__name__
            )

            try:
                api_json = json.dumps(
                    api_dict,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                )
            except Exception:
                api_json = repr(api_dict)

            if len(api_json) > 20000:
                api_json = (
                    api_json[:20000]
                    + "...[TRUNCATED]"
                )

            log.warning(
                "HFT_TEST3_API_DICT=%s",
                api_json
            )

            log.warning(
                "========== HFT_TEST3_DICT_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # ==========================================
        # CLIENT ENDPOINTS
        # ==========================================
        try:
            log.warning(
                "========== HFT_TEST3_ENDPOINTS_START =========="
            )

            endpoints = getattr(
                client,
                "endpoints",
                None
            )

            log.warning(
                "HFT_TEST3_ENDPOINTS_TYPE=%s",
                type(endpoints).__name__
            )

            log.warning(
                "HFT_TEST3_ENDPOINTS_REPR=%r",
                endpoints
            )

            if isinstance(endpoints, dict):
                for endpoint_key, endpoint_value in endpoints.items():

                    log.warning(
                        "ENDPOINT_KEY=%r",
                        endpoint_key
                    )

                    log.warning(
                        "ENDPOINT_TYPE=%s",
                        type(endpoint_value).__name__
                    )

                    endpoint_dict = getattr(
                        endpoint_value,
                        "__dict__",
                        None
                    )

                    if isinstance(
                        endpoint_dict,
                        dict
                    ):
                        for key, value in endpoint_dict.items():

                            try:
                                value_repr = repr(value)

                                if len(value_repr) > 5000:
                                    value_repr = (
                                        value_repr[:5000]
                                        + "...[TRUNCATED]"
                                    )

                                log.warning(
                                    "ENDPOINT_ATTR %s = %s",
                                    key,
                                    value_repr
                                )

                            except Exception as attr_error:
                                log.warning(
                                    "ENDPOINT_ATTR_ERROR %s = %s",
                                    key,
                                    safe_error_text(
                                        attr_error
                                    )
                                )

            log.warning(
                "========== HFT_TEST3_ENDPOINTS_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST3_ENDPOINTS_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # ==========================================
        # SPACE / API CONFIG
        # ==========================================
        log.warning(
            "HFT_TEST3_SPACE=%s",
            HF_SPACE
        )

        log.warning(
            "HFT_TEST3_API_NAME=%s",
            HF_API_NAME or "<AUTO>"
        )

        log.warning(
            "HFT_TEST3_FINISHED"
        )

        await update.message.reply_text(
            "✅ HFT_TEST3 خلص.\n\n"
            "الآن افتح Render Logs وابحث عن:\n"
            "HFT_TEST3"
        )

    except Exception as e:

        error = safe_error_text(e)

        log.error(
            "HFT_TEST3_FATAL_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ HFT_TEST3 ERROR:\n\n"
            + error[:1500]
        )
