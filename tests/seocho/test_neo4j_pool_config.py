"""Explicit pool settings reach the persistent driver without changing defaults."""

import sys
import types
from unittest.mock import Mock

import pytest

from seocho.store.graph import Neo4jGraphStore


@pytest.fixture
def driver(monkeypatch):
    factory = Mock(return_value=Mock())
    monkeypatch.setitem(
        sys.modules,
        "neo4j",
        types.SimpleNamespace(GraphDatabase=types.SimpleNamespace(driver=factory)),
    )
    return factory


def test_default_and_explicit_pool(driver):
    with Neo4jGraphStore("bolt://localhost", "user", "password"):
        driver.assert_called_once_with("bolt://localhost", auth=("user", "password"))
    driver.reset_mock()
    with Neo4jGraphStore(
        "bolt://localhost",
        "user",
        "password",
        max_connection_pool_size=16,
        connection_acquisition_timeout=2.5,
    ):
        driver.assert_called_once_with(
            "bolt://localhost",
            auth=("user", "password"),
            max_connection_pool_size=16,
            connection_acquisition_timeout=2.5,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_connection_pool_size": True},
        {"max_connection_pool_size": 0},
        {"max_connection_pool_size": 1.5},
        {"connection_acquisition_timeout": False},
        {"connection_acquisition_timeout": 0},
        {"connection_acquisition_timeout": float("inf")},
        {"connection_acquisition_timeout": float("nan")},
    ],
)
def test_invalid_options_never_construct_driver(driver, kwargs):
    with pytest.raises(ValueError):
        Neo4jGraphStore("bolt://localhost", "user", "password", **kwargs)
    driver.assert_not_called()
