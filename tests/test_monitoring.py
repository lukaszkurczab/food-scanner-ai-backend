from pytest_mock import MockerFixture

from app.core import monitoring


def test_init_sentry_skips_when_dsn_missing(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "")
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_skips_in_local_environment(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "local")
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_skips_during_pytest(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "production")
    mocker.patch.object(monitoring, "_running_under_pytest", return_value=True)
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_uses_configured_sentry_environment(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "production")
    mocker.patch.object(monitoring.settings, "SENTRY_ENVIRONMENT", "staging")
    mocker.patch.object(monitoring.settings, "VERSION", "1.2.3")
    mocker.patch.object(monitoring, "_running_under_pytest", return_value=False)
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    assert sentry_init.call_count == 1
    kwargs = sentry_init.call_args.kwargs
    assert kwargs["dsn"] == "https://example@sentry.io/1"
    assert kwargs["environment"] == "staging"
    assert kwargs["release"] == "1.2.3"
    assert kwargs["send_default_pii"] is False
    assert kwargs["traces_sample_rate"] == 0.1
    assert len(kwargs["integrations"]) == 2
