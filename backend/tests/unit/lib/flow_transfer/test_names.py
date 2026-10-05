from src.lib.flow_transfer.importer import copy_name


def test_a_free_name_is_kept():
    assert copy_name("Finder", set().__contains__, max_chars=255) == "Finder"


def test_copy_name_follows_the_copy_convention():
    taken = {"Finder", "Finder (Copy)"}
    assert copy_name("Finder", taken.__contains__, max_chars=255) == "Finder (Copy 2)"


def test_copy_name_fits_the_limit():
    long = "a" * 255
    taken = {long, long[:248] + " (Copy)"}
    name = copy_name(long, taken.__contains__, max_chars=255)
    assert name == long[:246] + " (Copy 2)" and len(name) == 255
