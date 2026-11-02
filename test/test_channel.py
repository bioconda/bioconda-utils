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


def test_config_stores_typed_channels_without_changing_them_on_read():
    channels = [Channel("bioconda")]
    config = Config(channels=channels, upload_channel=Channel("bioconda"))
    assert config.channels is channels
    assert config.channels[0] is channels[0]
    assert isinstance(config.upload_channel, Channel)
    config.channels.append(Channel("conda-forge"))
    assert config.channels == [Channel("bioconda"), Channel("conda-forge")]


@pytest.mark.parametrize("channels", ["bioconda", ["bioconda"], [" "]])
def test_config_rejects_unparsed_channels(channels):
    with pytest.raises(TypeError, match="must be a list of Channel"):
        Config(channels=channels)


@pytest.mark.parametrize("channel", ["bioconda", " ", None])
def test_config_rejects_unparsed_upload_channel(channel):
    with pytest.raises(TypeError, match="upload_channel must be a Channel"):
        Config(upload_channel=channel)


def test_normalized_config_contains_typed_channels_before_reading():
    config = normalize_config(
        {
            "channels": [" bioconda/ "],
            "upload_channel": " bioconda/ ",
        }
    )
    assert isinstance(config.channels[0], Channel)
    assert isinstance(config.upload_channel, Channel)
    assert config.channels == [Channel("bioconda")]


def test_config_defaults_do_not_share_mutable_lists():
    first, second = Config(), Config()
    first.channels.append(Channel("example"))
    assert Channel("example") not in second.channels


def test_config_preserves_typed_channels_through_process_pickling():
    config = normalize_config({"channels": [" bioconda/ "]})
    restored = pickle.loads(pickle.dumps(config))
    assert restored == config
    assert isinstance(restored.channels[0], Channel)
    assert isinstance(restored.upload_channel, Channel)
