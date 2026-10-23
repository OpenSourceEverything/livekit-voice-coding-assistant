"""Reproduce the SIP startup thread boundary without models or provider calls."""

import asyncio
import builtins
import threading
from types import SimpleNamespace

import pytest
from livekit.agents import Plugin

import agent


@pytest.fixture
def fake_silero(monkeypatch):
    """Keep the installed SDK registration check, replacing only model loading."""
    events = []
    model = object()
    original_import = builtins.__import__
    monkeypatch.setattr(Plugin, "registered_plugins", [])
    monkeypatch.setattr(Plugin, "emitter", SimpleNamespace(emit=lambda *args: None))

    def load():
        events.append(("load", threading.current_thread()))
        return model

    plugin = SimpleNamespace(VAD=SimpleNamespace(load=load))
    registered = False

    def guarded_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        nonlocal registered
        if name == "livekit.plugins" and "silero" in fromlist:
            events.append(("import", threading.current_thread()))
            # This is the real installed SDK guard that failed in the SIP job.
            if not registered:
                Plugin.register_plugin(Plugin("Synthetic VAD", "0", "synthetic"))
                registered = True
            return SimpleNamespace(silero=plugin)
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return SimpleNamespace(events=events, model=model, load=load)


def test_worker_start_registers_on_main_without_loading(fake_silero):
    agent.register_voice_plugins()
    assert fake_silero.events == [("import", threading.main_thread())]
    assert len(Plugin.registered_plugins) == 1


@pytest.mark.asyncio
async def test_console_job_thread_reuses_main_thread_registration(fake_silero):
    agent.register_voice_plugins()
    model = await asyncio.to_thread(lambda: asyncio.run(agent.prepare_vad_model()))
    assert model is fake_silero.model
    assert fake_silero.events[0][1] is threading.main_thread()
    assert fake_silero.events[1][1] is not threading.main_thread()
    assert len(Plugin.registered_plugins) == 1


def test_get_vad_loader_imports_on_main_thread_without_loading_model(fake_silero):
    loader = agent.get_vad_loader()

    assert loader is fake_silero.load
    assert fake_silero.events == [("import", threading.main_thread())]
    assert len(Plugin.registered_plugins) == 1


@pytest.mark.asyncio
async def test_prepare_vad_registers_on_main_thread_and_loads_in_executor(fake_silero):
    model = await agent.prepare_vad_model()

    assert model is fake_silero.model
    assert [event for event, _ in fake_silero.events] == ["import", "load"]
    assert fake_silero.events[0][1] is threading.main_thread()
    assert fake_silero.events[1][1] is not threading.main_thread()
    assert len(Plugin.registered_plugins) == 1


@pytest.mark.asyncio
async def test_old_import_inside_executor_reproduces_installed_sdk_failure(fake_silero):
    def old_import_and_load():
        from livekit.plugins import silero

        return silero.VAD.load()

    with pytest.raises(
        RuntimeError, match="Plugins must be registered on the main thread"
    ):
        await asyncio.to_thread(old_import_and_load)

    assert [event for event, _ in fake_silero.events] == ["import"]
    assert fake_silero.events[0][1] is not threading.main_thread()
    assert Plugin.registered_plugins == []


@pytest.mark.asyncio
async def test_prepare_vad_load_does_not_block_main_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    model = object()

    def load():
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("Synthetic test model was not released")
        return model

    def loader():
        assert threading.current_thread() is threading.main_thread()
        return load

    monkeypatch.setattr(agent, "get_vad_loader", loader)
    task = asyncio.create_task(agent.prepare_vad_model())
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0)
        # This continuation runs while the expensive model is still loading.
        assert not task.done()
    finally:
        release.set()
        result = await task
    assert result is model


@pytest.mark.asyncio
async def test_prepare_vad_reports_model_load_failure(monkeypatch):
    def fail_load():
        raise RuntimeError("Synthetic model initialization failure")

    monkeypatch.setattr(agent, "get_vad_loader", lambda: fail_load)
    with pytest.raises(RuntimeError, match="Synthetic model initialization failure"):
        await agent.prepare_vad_model()
