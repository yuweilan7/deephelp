"""Frozen synthetic SOP mechanisms; shared offline/live cases, never enterprise data."""

from deephelp_tools.assets import asset_path
from deephelp_tools.demo.scenarios import SOPScenario, SOPScenarioSet, fixtures
from deephelp_tools.demo.scenarios import proposal_registry as proposal_registry
from deephelp_tools.demo.tool_config import MockConfig


def scenarios() -> SOPScenarioSet:
    result = SOPScenarioSet.model_validate_json(
        asset_path("scenarios-v1.json").read_text(encoding="utf-8")
    )
    if not result.synthetic or result.version != "m14-scenarios-v1":
        raise ValueError("Only frozen M14 synthetic scenarios are supported")
    return result


def scenario_config(case: SOPScenario) -> MockConfig:
    faults = {}
    tool_faults = {}
    if case.fault:
        if case.fault_tool:
            tool_faults[case.fault_tool.value + ":" + case.order] = case.fault
        else:
            faults[case.order] = case.fault
    return MockConfig(fixtures=fixtures(), faults=faults, tool_faults=tool_faults)
