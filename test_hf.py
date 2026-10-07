async def hf_test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    await update.message.reply_text(
        "🔎 بفحص API الخاصة بـ Hugging Face بالتفصيل..."
    )

    try:
        client = get_hf_client()

        log.info("========== HF_API_SCHEMA_RAW ==========")

        # 1) العرض النصي الكامل
        try:
            api_text = client.view_api()
            log.info("VIEW_API_TEXT=%r", api_text)
        except Exception as e:
            log.error(
                "VIEW_API_TEXT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # 2) العرض بصيغة dict
        try:
            api_dict = client.view_api(return_format="dict")
            log.info(
                "VIEW_API_DICT_TYPE=%s",
                type(api_dict).__name__
            )
            log.info(
                "VIEW_API_DICT=%s",
                json.dumps(
                    api_dict,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                )
            )
        except Exception as e:
            log.error(
                "VIEW_API_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # 3) محاولة عرض endpoints إن كانت متاحة
        try:
            endpoints = getattr(client, "endpoints", None)
            log.info(
                "CLIENT_ENDPOINTS=%r",
                endpoints
            )
        except Exception as e:
            log.error(
                "CLIENT_ENDPOINTS_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.info("========== HF_API_SCHEMA_RAW_END ==========")

        await update.message.reply_text(
            "✅ خلص الفحص التفصيلي.\n\n"
            "ابعتلي من Render Logs كل شيء بين:\n"
            "HF_API_SCHEMA_RAW\n"
            "و\n"
            "HF_API_SCHEMA_RAW_END"
        )

    except Exception as e:
        error = safe_error_text(e)

        log.error(
            "HF_SCHEMA_TEST_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ فشل فحص HF:\n\n"
            + error[:1500]
        )
