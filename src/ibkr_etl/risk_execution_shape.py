"""Exact risk-canary execution comparison, including known ARM representations.

This validates metadata only. It grants no deployment or database authority.
"""
import copy


JOB = "ibkrv2stg-risk-snapshots"
CONTAINER = "risk-snapshots"


def validate_risk_execution(actual, wanted):
    if (not isinstance(actual, list) or not isinstance(wanted, list)
            or len(actual) != 1 or len(wanted) != 1
            or not isinstance(actual[0], dict) or not isinstance(wanted[0], dict)):
        raise ValueError("One exact risk container required")
    observed = copy.deepcopy(actual)
    expected = copy.deepcopy(wanted)
    container, original = observed[0], expected[0]
    if original.get("name") != CONTAINER:
        raise ValueError("Exact risk job required")
    env = container.get("env")
    expected_env = original.get("env")
    if not isinstance(env, list) or not isinstance(expected_env, list) or len(env) != len(expected_env):
        raise ValueError("Exact risk environment required")
    database = [i for i, item in enumerate(expected_env)
                if isinstance(item, dict) and item.get("name") == "DATABASE_URL"]
    if len(database) != 1:
        raise ValueError("One protected database binding required")
    index = database[0]
    binding = expected_env[index]
    if (binding.get("secretRef") != "database-url"
            or binding.get("value", "") != ""
            or not isinstance(env[index], dict)):
        raise ValueError("Expected protected database reference required")
    # Azure's native execution snapshot aliases the existing job binding. No
    # arbitrary aliases, values, destinations or added environment are allowed.
    if env[index].get("secretRef") == "cappjob-" + JOB:
        if env[index].get("name") != "DATABASE_URL" or "value" in env[index]:
            raise ValueError("Native reference must not contain a value")
        expected_env[index]["secretRef"] = "cappjob-" + JOB
        expected_env[index].pop("value", None)
    if "imageType" in container and "imageType" not in original:
        if container["imageType"] != "ContainerImage":
            raise ValueError("Unexpected native image type")
        container.pop("imageType")
    resources = container.get("resources")
    if not isinstance(resources, dict) or not isinstance(original.get("resources"), dict):
        raise ValueError("Exact resources required")
    if "ephemeralStorage" not in original["resources"] and resources.get("ephemeralStorage") == "":
        resources.pop("ephemeralStorage")
    if observed != expected:
        raise ValueError("Native risk execution differs from reviewed request")
    return True
