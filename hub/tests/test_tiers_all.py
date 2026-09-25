"""Every registered action must resolve to a valid tier with no arguments - independent of
CONTROL_LIVE, so it runs in normal CI too."""
from control import registry


def test_every_registered_action_has_a_valid_tier(clean_registry):
    from control.areas import core
    core.register_area({})

    try:
        import wadlib  # noqa: F401
    except ImportError:
        pass
    else:
        from control.areas import win
        win.register_area({})

    try:
        import xl2ai  # noqa: F401
    except ImportError:
        pass
    else:
        from control.areas import data
        data.register_area({})

    assert registry.ACTIONS
    for name, action in registry.ACTIONS.items():
        assert action.tier_for({}) in registry.TIERS, name
