import pytest

from cura_ai.catalogue import Catalogue, DefinitionNotFound


@pytest.fixture(scope="session")
def flsun() -> Catalogue:
    """Flsun Q5 from the installed Cura's definition files; skipped when Cura is absent."""
    try:
        return Catalogue.from_definitions("flsun_q5")
    except DefinitionNotFound:
        pytest.skip("Cura definitions not installed")
