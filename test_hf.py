async def hf_test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    log.warning("HFT_TEST2_NEW_CODE_2026")

    await update.message.reply_text(
        "🧪 HFT_TEST2 وصل للنسخة الجديدة من الكود ✅"
    )

    try:
        client = get_hf_client()

        log.warning("HF CLIENT CREATED")

        # ==============================
        # API TEXT
        # ==============================
        try:
            api_text = client.view_api()

            log.warning(
                "========== HFT_TEST2_API_TEXT =========="
            )

            log.warning(
                "%r",
                api_text
            )

            log.warning(
                "========== HFT_TEST2_API_TEXT_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST2_VIEW_API_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        # ==============================
        # API DICT
        # ==============================
        try:
            api_dict = client.view_api(
                return_format="dict"
            )

            log.warning(
                "========== HFT_TEST2_API_DICT =========="
            )

            log.warning(
                "%s",
                json.dumps(
                    api_dict,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                )
            )

            log.warning(
                "========== HFT_TEST2_API_DICT_END =========="
            )

        except Exception as e:
            log.error(
                "HFT_TEST2_DICT_ERROR=%s",
                safe_error_text(e),
                exc_info=True
            )

        await update.message.reply_text(
            "✅ HFT_TEST2 خلص.\n\n"
            "افتح Render Logs وابحث عن:\n"
            "HFT_TEST2"
        )

    except Exception as e:
        error = safe_error_text(e)

        log.error(
            "HFT_TEST2_ERROR=%s",
            error,
            exc_info=True
        )

        await update.message.reply_text(
            "❌ HFT_TEST2 ERROR:\n\n"
            + error[:1500]
        )
