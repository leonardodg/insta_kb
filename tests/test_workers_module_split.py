"""O fatiamento de `ig_worker.py` em worker/* é contratado aqui.

O audit (F1/SRP) exige ~4 módulos com responsabilidades separadas e o
`ig_worker.py` reduzido à orquestração, mantendo a superfície pública
(`mod.strip_cta`, `mod.existing_media`, scripts, `python -m workers.ig_worker`)
intacta. Os testes de comportamento continuam em test_workers_ig_worker.py;
este arquivo só garante ONDE o código vive (re-exportar não basta: o
`__module__` precisa apontar pro módulo novo, senão o fatiamento é fachada).
"""

from __future__ import annotations


def test_text_module_holds_cta_and_title_logic():
    from workers import text  # noqa: PLC0415 -- RED é o ImportError deste teste

    assert text.strip_cta.__module__ == "workers.text"
    assert text.clean_title.__module__ == "workers.text"


def test_media_download_module_holds_download_and_disk_logic():
    from workers import media_download  # noqa: PLC0415

    assert media_download.existing_media.__module__ == "workers.media_download"
    assert media_download.default_download.__module__ == (  # pyright: ignore[reportPrivateUsage] -- structural contract
        "workers.media_download"
    )
    assert media_download.classify_file.__module__ == "workers.media_download"


def test_screen_module_holds_ffmpeg_and_ocr_logic():
    from workers import screen  # noqa: PLC0415

    assert screen.extract_frames.__module__ == "workers.screen"
    assert screen.merge_screen_text.__module__ == "workers.screen"
    assert screen.default_read_screen.__module__ == (  # pyright: ignore[reportPrivateUsage] -- structural contract
        "workers.screen"
    )


def test_consumer_module_holds_amqp_state_and_pace_logic():
    from workers import consumer  # noqa: PLC0415

    assert consumer.apply_command.__module__ == "workers.consumer"
    assert consumer.pace_sleep_seconds.__module__ == "workers.consumer"
    assert consumer.record_progress.__module__ == (  # pyright: ignore[reportPrivateUsage] -- structural contract
        "workers.consumer"
    )


def test_ig_worker_keeps_the_compatible_surface_for_scripts_and_tests():
    from workers import (  # noqa: PLC0415
        consumer,
        ig_worker,
        media_download,
        screen,
        text,
    )

    assert ig_worker.strip_cta is text.strip_cta
    assert ig_worker.clean_title is text.clean_title
    assert ig_worker.existing_media is media_download.existing_media
    assert ig_worker._default_download is media_download.default_download  # pyright: ignore[reportPrivateUsage] -- scripts/ig_reprocessar.py usam via mod
    assert ig_worker.merge_screen_text is screen.merge_screen_text
    assert ig_worker._default_read_screen is screen.default_read_screen  # pyright: ignore[reportPrivateUsage] -- scripts/ig_reprocessar.py usam via mod
    assert ig_worker.apply_command is consumer.apply_command
    assert ig_worker.pace_sleep_seconds is consumer.pace_sleep_seconds


def test_ig_worker_still_exposes_the_daemon_entrypoint():
    from workers import ig_worker  # noqa: PLC0415

    assert callable(ig_worker.run)
    assert callable(ig_worker.process_message)
