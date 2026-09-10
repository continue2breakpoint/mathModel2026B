from mathmodel2026b.geometry import Point
from mathmodel2026b.state import ChannelState, ChannelStatus, DogState


def test_channel_state_transition() -> None:
    state = ChannelState(3)
    assert state.status == ChannelStatus.UNKNOWN
    state.add_bearing(Point(0, 0), 30.0)
    assert state.status == ChannelStatus.DETECTED
    state.add_bearing(Point(100, 0), 40.0)
    assert state.status == ChannelStatus.LOCALIZING
    state.mark_cleared()
    assert state.status == ChannelStatus.CLEARED


def test_dog_has_20_channels() -> None:
    assert set(DogState().channels) == set(range(1, 21))
