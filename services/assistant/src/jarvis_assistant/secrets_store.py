from __future__ import annotations


class CredentialStoreUnavailable(RuntimeError):
    pass


class WindowsCredentialStore:
    """Optional production secret storage backed by the user's Windows Credential Manager."""

    _SERVICE = "JarvisAssistant"
    _ALLOWED_NAMES = frozenset({"DEEPGRAM_API_KEY", "GEMINI_API_KEY"})

    @classmethod
    def get(cls, name: str) -> str | None:
        cls._validate_name(name)
        try:
            import keyring
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                "install the 'secrets' extra to use Credential Manager"
            ) from exc
        return keyring.get_password(cls._SERVICE, name)

    @classmethod
    def set(cls, name: str, value: str) -> None:
        cls._validate_name(name)
        if not value:
            raise ValueError("secret cannot be empty")
        try:
            import keyring
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                "install the 'secrets' extra to use Credential Manager"
            ) from exc
        keyring.set_password(cls._SERVICE, name, value)

    @classmethod
    def delete(cls, name: str) -> None:
        cls._validate_name(name)
        try:
            import keyring
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                "install the 'secrets' extra to use Credential Manager"
            ) from exc
        try:
            keyring.delete_password(cls._SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            return

    @classmethod
    def _validate_name(cls, name: str) -> None:
        if name not in cls._ALLOWED_NAMES:
            raise ValueError("credential name is not allowlisted")
