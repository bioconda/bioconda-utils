"""Channel construction and compatibility at configuration/storage boundaries."""

import json
import pickle
import sqlite3

import pytest

from bioconda_utils._types import Channel, Config
from bioconda_utils.config import normalize_config


@pytest.mark.parametrize("value", [None, 42, b"bioconda", ["bioconda"]])
def test_channel_rejects_non_strings(value):
    with pytest.raises(TypeError, match="must be strings"):
        Channel(value)


def test_channel_preserves_type_through_process_pickling():
    channel = Channel("bioconda")
    restored = pickle.loads(pickle.dumps(channel))
    assert isinstance(restored, Channel)
    assert restored == channel


def test_channel_works_with_json_and_sqlite():
    channel = Channel("bioconda")
    assert json.loads(json.dumps({"channel": channel})) == {"channel": "bioconda"}
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT ?", (channel,)).fetchone() == ("bioconda",)


def test_config_accessors_validate_raw_channels_and_preserve_list_identity():
    channels = [" bioconda/ "]
    config = Config({"channels": channels, "upload_channel": " bioconda/ "})
    assert config.channels is channels
    assert isinstance(config.channels[0], Channel)
    assert config.channels == ["bioconda"]
    assert isinstance(config.upload_channel, Channel)
    assert config.upload_channel == "bioconda"
    config.channels.append(Channel("conda-forge"))
    assert config.channels == ["bioconda", "conda-forge"]


@pytest.mark.parametrize("key", ["channels", "upload_channel"])
def test_config_accessors_reject_invalid_mutations(key):
    config = normalize_config({})
    if key == "channels":
        config["channels"].append(" ")
    else:
        config["upload_channel"] = " "
    with pytest.raises(ValueError, match="must not be empty"):
        getattr(config, key)


def test_config_channels_rejects_a_scalar():
    with pytest.raises(TypeError, match="must be a list"):
        _ = Config({"channels": "bioconda"}).channels
