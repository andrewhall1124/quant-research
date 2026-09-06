import pytest

from voltium.loaders import DataPaths


@pytest.fixture(scope="session")
def data_paths() -> DataPaths:
    try:
        return DataPaths.from_repo()
    except FileNotFoundError:
        pytest.skip("no data_store/ available")
