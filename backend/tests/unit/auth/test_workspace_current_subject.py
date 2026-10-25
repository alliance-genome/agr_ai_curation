"""Subject discovery never substitutes for authoritative account/group checks."""

from unittest.mock import Mock
import pytest
from auth_runtime.base import CurrentPrincipalDenied
from src.auth.providers.cognito_current_principal import _lookup_subject


def client():
    api = Mock()
    api.list_users.return_value = {
        "Users": [
            {"Username": "username", "Attributes": [{"Name": "sub", "Value": "owner"}]}
        ]
    }
    api.admin_get_user.return_value = {
        "Enabled": True,
        "Username": "username",
        "UserAttributes": [{"Name": "sub", "Value": "owner"}],
    }
    api.admin_list_groups_for_user.side_effect = [
        {"Groups": [{"GroupName": "first"}], "NextToken": "next"},
        {"Groups": [{"GroupName": "second"}]},
    ]
    return api


def test_subject_discovery_checks_authoritative_account_and_all_group_pages():
    api = client()
    principal = _lookup_subject("owner", client=api, pool_id="pool", issuer="issuer")
    assert principal.subject == "owner" and principal.groups == ["first", "second"]
    api.list_users.assert_called_once_with(UserPoolId="pool", Filter='sub = "owner"')
    api.admin_get_user.assert_called_once_with(UserPoolId="pool", Username="username")
    assert api.admin_list_groups_for_user.call_count == 2


@pytest.mark.parametrize(
    "case", ["missing", "ambiguous", "mismatch", "disabled", "changed_subject"]
)
def test_subject_lookup_fails_closed(case):
    api = client()
    if case == "missing":
        api.list_users.return_value["Users"] = []
    elif case == "ambiguous":
        api.list_users.return_value["Users"].append(
            {"Username": "other", "Attributes": [{"Name": "sub", "Value": "owner"}]}
        )
    elif case == "mismatch":
        api.list_users.return_value["Users"][0]["Attributes"][0]["Value"] = "other"
    elif case == "disabled":
        api.admin_get_user.return_value["Enabled"] = False
    else:
        api.admin_get_user.return_value["UserAttributes"][0]["Value"] = "other"
    with pytest.raises(CurrentPrincipalDenied):
        _lookup_subject("owner", client=api, pool_id="pool", issuer="issuer")
    api.admin_list_groups_for_user.assert_not_called()
