# -*- coding: utf-8 -*-
"""TTS model service: builds a TTSModelBase from stored credential + config."""
from typing import Any, Type

from fastapi import HTTPException, status

from ._access import ResourceAccessService
from ..storage import TTSModelConfig
from ...credential import CredentialFactory, DashScopeCredential, OpenAICredential
from ...tts import TTSModelBase


def _needs_bailian_tts_client(model: str) -> bool:
    """True when the model speaks Bailian-style TTS APIs (not OpenAI /audio/speech).

    Selection is by model id / API family only — gateway host is never inspected.
    """
    mid = (model or "").lower()
    if "cosyvoice" in mid:
        return True
    if "qwen" in mid and ("tts" in mid or "speech" in mid):
        return True
    if "minimax" in mid or "speech-2." in mid or "speech-02" in mid:
        return True
    return False


def _as_bailian_tts_credential(credential: Any) -> DashScopeCredential:
    """Reuse the bound gateway's api_key/base_url with Bailian TTS clients.

    AgentScope names this credential type ``dashscope_credential``; the
    endpoint remains whatever the platform gateway configured.
    """
    if isinstance(credential, DashScopeCredential):
        return credential
    api_key = getattr(credential, "api_key", None)
    base_url = getattr(credential, "base_url", None)
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "TTS credential is missing base_url; bind the voice model to a "
                "gateway that exposes the model's TTS API."
            ),
        )
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
        "name": getattr(credential, "name", "") or "",
    }
    cred_id = getattr(credential, "id", None)
    if cred_id:
        kwargs["id"] = cred_id
    return DashScopeCredential(**kwargs)


async def get_tts_model(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
    *,
    stream: bool | None = None,
) -> TTSModelBase:
    """Build a TTS model instance from a stored credential and config.

    Args:
        user_id (`str`):
            The viewer's user id (may differ from the credential owner
            for shared credentials).
        config (`TTSModelConfig`):
            The TTS model configuration.
        access (`ResourceAccessService`):
            Injected resource access service.
        stream (`bool | None`):
            When set, overrides the TTS class default. IM non-realtime
            replies should pass ``False`` so a finished WAV is produced
            (streaming headers break DingTalk voice playback).

    Returns:
        `TTSModelBase`:
            The TTS model instance.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    # Historical publishes typed Bailian TTS models as openai_credential
    # (gateway protocol=openai). OpenAI /audio/speech is the wrong client —
    # switch client stack, keep the gateway's own base_url/api_key.
    if _needs_bailian_tts_client(config.model) and isinstance(
        credential,
        OpenAICredential,
    ):
        credential = _as_bailian_tts_credential(credential)

    tts_classes = credential.get_tts_model_classes()
    if not tts_classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {config.type!r} does not support TTS models.",
        )

    tts_cls = _resolve_tts_class(tts_classes, config.model)
    parameters = (
        tts_cls.Parameters(**config.parameters) if config.parameters else None
    )
    kwargs: dict[str, Any] = {
        "credential": credential,
        "model": config.model,
        "parameters": parameters,
    }
    if stream is not None:
        kwargs["stream"] = stream
    return tts_cls(**kwargs)


def _resolve_tts_class(
    classes: list[Type[TTSModelBase]],
    model: str,
) -> Type[TTSModelBase]:
    """Pick the TTS class that lists the given model name."""
    for cls in classes:
        if any(card.name == model for card in cls.list_models()):
            return cls
    mid = (model or "").lower()
    # Heuristic when YAML cards don't list every deployed model id.
    for cls in classes:
        name = cls.__name__.lower()
        if "cosyvoice" in mid and "cosyvoice" in name:
            return cls
        if "realtime" in mid and "realtime" in name:
            return cls
    for cls in classes:
        name = cls.__name__.lower()
        if "cosyvoice" in name or "realtime" in name:
            continue
        # Non-realtime Bailian Qwen-TTS client (class name contains DashScope).
        if "tts" in name and "openai" not in name:
            return cls
    return classes[0]
