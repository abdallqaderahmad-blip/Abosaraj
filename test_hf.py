async def hf_test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    # علامة واضحة جدًا للتأكد أن Render يشغّل الكود الجديد
    log.info("🔥🔥🔥 NEW_HFTEST_CODE_IS_RUNNING 🔥🔥🔥")

    await update.message.reply_text(
        "🔎 بفحص API الخاصة بـ Hugging Face بالتفصيل..."
    )

    try:
        client = get_hf_client()

        log.info("========== HF_API_SCHEMA_RAW ==========")

        # 1) العرض النصي الكامل للـ API
        try:
            api_text = client.view_api()

            log.info(
                "VIEW_API_TEXT_TYPE=%s",
                type(api_text).__name__
            )

            log.info(
                "VIEW_API_TEXT=%r",
                api_text
            )

        except Exception as e:
            log.error(
                "VIEW_API_TEXT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # 2) العرض بصيغة Dictionary
        try:
            api_dict = client.view_api(
                return_format="dict"
            )

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

        # 3) عرض endpoints الموجودة داخل Client
        try:
            endpoints = getattr(
                client,
                "endpoints",
                None
            )

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

        # 4) معلومات إضافية عن الـ Client
        try:
            log.info(
                "CLIENT_TYPE=%s",
                type(client).__name__
            )

            log.info(
                "CLIENT_SPACE=%s",
                HF_SPACE
            )

        except Exception as e:
            log.error(
                "CLIENT_INFO_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        log.info(
            "========== HF_API_SCHEMA_RAW_END =========="
        )

        await update.message.reply_text(
            "✅ خلص الفحص التفصيلي.\n\n"
            "هسا افتح Render Logs وابحث عن:\n\n"
            "🔥 NEW_HFTEST_CODE_IS_RUNNING\n\n"
            "وبعدها:\n"
            "HF_API_SCHEMA_RAW\n\n"
            "وابعتلي كل الناتج."
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
