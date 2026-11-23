import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from deploy_eval_v2.top_input import validate as validate_top_input

AGENTS = Path(__file__).resolve().parents[2]
SCHEMAS = Path(__file__).resolve().parents[1] / "src" / "deploy_eval_v2" / "schemas"
SKILLS = AGENTS / "skills"
TEMPLATES = SKILLS / "ascend-llm-plan" / "templates"
TEMPLATE_DOC = SKILLS / "ascend-llm-plan" / "references" / "templates.md"
TOKEN = re.compile(r"\{\{([a-z0-9][a-z0-9._]*)\}\}")
SITE_BINDINGS = {
    "gpu.0": "6",
    "gpu.1": "7",
    "dataset.gpqa.root": "/home/baoyifan/dataset/gpqa",
}


def load(name):
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


def validator(schema_name, def_name):
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    return Draft202012Validator({"$ref": "#/$defs/" + def_name, "$defs": schema["$defs"]})


def fill(value, bindings=None):
    bindings = SITE_BINDINGS if bindings is None else bindings
    if isinstance(value, dict):
        return {fill(k, bindings): fill(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, bindings) for v in value]
    if isinstance(value, str):
        return TOKEN.sub(lambda m: bindings.get(m.group(1), m.group(0)), value)
    return value


def template_tokens():
    tokens = set()
    for path in TEMPLATES.glob("*.json"):
        tokens.update(TOKEN.findall(path.read_text(encoding="utf-8")))
    return tokens


class TemplateTest(unittest.TestCase):
    def test_acc_case_matches_schema(self):
        validator("external-eval-plan.schema.json", "case").validate(load("eval-acc-gpqa.case.json"))

    def test_acc_mini_case_matches_schema(self):
        case = load("eval-acc-gpqa-mini.case.json")
        validator("external-eval-plan.schema.json", "case").validate(case)
        self.assertEqual(case["num_prompts"], 30)

    def test_perf_case_matches_schema(self):
        validator("external-eval-plan.schema.json", "case").validate(load("eval-perf-synthetic.case.json"))

    def test_acc_case_rejects_mode_switch(self):
        case = load("eval-acc-gpqa.case.json")
        case["mode"] = "perf"
        with self.assertRaises(Exception):
            validator("external-eval-plan.schema.json", "case").validate(case)

    def test_acc_case_rejects_unknown_field(self):
        case = load("eval-acc-gpqa.case.json")
        case["temperature"] = 0
        with self.assertRaises(Exception):
            validator("external-eval-plan.schema.json", "case").validate(case)

    def test_dataset_binding_matches_schema_after_fill(self):
        validator("eval-bindings.schema.json", "dataset").validate(fill(load("dataset-aisbench-task.json")))
        validator("eval-bindings.schema.json", "dataset").validate(load("dataset-synthetic.json"))

    def test_templates_carry_no_site_values(self):
        runtime = load("deploy-runtime-qwen3-8b.json")
        self.assertNotIn("sha256:", runtime["image"])
        self.assertEqual(
            [d for d in runtime["container"]["devices"] if re.search(r"davinci\d", d)],
            [],
        )
        instance = load("deploy-instance-qwen3-8b-tp2.json")
        self.assertTrue(instance["placement"]["node"].startswith("{{"))
        for device in instance["placement"]["devices"]:
            self.assertTrue(device.startswith("{{"), device)
        self.assertTrue(instance["launch"]["env"]["HCCL_IF_BASE_PORT"].startswith("{{"))
        self.assertTrue(load("dataset-aisbench-task.json")["path"].startswith("{{"))

    def test_launch_profile_is_consistent_after_fill(self):
        runtime = fill(load("deploy-runtime-qwen3-8b.json"))
        instance = fill(load("deploy-instance-qwen3-8b-tp2.json"))
        placement = instance["placement"]["devices"]
        args = instance["launch"]["args"]
        davinci = [
            int(d.rsplit("davinci", 1)[1])
            for d in runtime["container"]["devices"]
            if re.fullmatch(r"/dev/davinci\d+", d)
        ]
        self.assertEqual(args["tensor-parallel-size"], len(placement))
        self.assertEqual(davinci, [int(x) for x in placement])
        self.assertLess(args["gpu-memory-utilization"], 0.8)
        self.assertLessEqual(args["max-model-len"], 16384)
        self.assertIn("HCCL_IF_BASE_PORT", instance["launch"]["env"])

    def test_deploy_fragments_embed_whole_objects(self):
        self.assertEqual(set(load("deploy-runtime-qwen3-8b.json")), {"image", "container"})
        instance = load("deploy-instance-qwen3-8b-tp2.json")
        self.assertTrue({"id", "role", "placement", "launch", "ports", "checks"} <= set(instance))

    def test_every_template_placeholder_is_documented(self):
        doc = TEMPLATE_DOC.read_text(encoding="utf-8")
        for token in template_tokens():
            self.assertIn("{{" + token + "}}", doc, "undocumented placeholder: " + token)

    def test_documented_placeholders_exist_in_templates(self):
        documented = set(TOKEN.findall(TEMPLATE_DOC.read_text(encoding="utf-8")))
        for token in documented:
            self.assertIn(token, template_tokens(), "stale placeholder in doc: " + token)

    def test_skills_reference_templates_and_grep_rule(self):
        plan_skill = (SKILLS / "ascend-llm-plan" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/templates.md", plan_skill)
        self.assertIn('grep -n "{{"', plan_skill)
        self.assertIn("Select-String", plan_skill)
        for stage in ("ascend-llm-deployment", "ascend-llm-eval", "ascend-llm-release"):
            text = (SKILLS / stage / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("references/requests.md", text)
            self.assertTrue((SKILLS / stage / "references" / "requests.md").is_file())


ROUTER = SKILLS / "ascend-llm-router"
TOP_DOC = ROUTER / "references" / "top-input.md"
TOP_DIR = ROUTER / "references" / "top-input"
SCENARIOS = ROUTER / "references" / "scenarios.json"
PLAYBOOKS = ROUTER / "references" / "playbooks"
EXAMPLES = ROUTER / "references" / "examples"
EXAMPLE_NAMES = {
    "colocated-existing-deploy-only.json",
    "colocated-existing-deploy-eval-perf.json",
    "colocated-existing-deploy-eval-acc.json",
    "colocated-owned-deploy-eval-acc.json",
    "pd-dual-host-existing-deploy-only.json",
    "eval-only-external-acc.json",
}
EVAL_ACTIONS = {"deploy_and_eval", "eval_only"}


def check_example(data):
    """Examples must pass the shared validator; extra checks here are doc hygiene."""
    report = validate_top_input(data)
    assert report["valid"], report["issues"]
    hosts = [node["host"].split("@")[-1] for node in data["nodes"].values()]
    if data["task"]["action"] in EVAL_ACTIONS:
        hosts.append(data["evaluation"]["tester"]["host"].split("@")[-1])
    for host in hosts:
        assert host.startswith("192.0.2."), "example host must be illustrative: " + host


class TopInputDocTest(unittest.TestCase):
    def setUp(self):
        self.doc = TOP_DOC.read_text(encoding="utf-8")

    def test_router_references_contract(self):
        text = (ROUTER / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/top-input.md", text)
        self.assertTrue(TOP_DOC.is_file())

    def test_control_json_is_not_an_entry(self):
        self.assertIn("不作为独立入口", self.doc)
        router_skill = (ROUTER / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("不作为独立入口", router_skill)
        plan_skill = (SKILLS / "ascend-llm-plan" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("用户已预置 control.json", plan_skill)

    def test_doc_covers_both_topologies(self):
        text = "\n".join(
            (ROUTER / "references" / name).read_text(encoding="utf-8")
            for name in ("top-input.md", "top-input/contract.md",
                         "top-input/derivation.md", "top-input/validation.md")
        )
        for word in ("单机 PD", "双机 PD", "proxy_program", "evaluation.params"):
            self.assertIn(word, text)
        self.assertIn("只照抄结构", self.doc)

    def test_index_stays_small_and_links_detail(self):
        self.assertLess(len(self.doc.splitlines()), 100, "top-input.md must stay a routing index")
        for name in ("top-input/contract.md", "top-input/derivation.md",
                     "top-input/validation.md", "scenarios.json"):
            self.assertIn(name, self.doc)
        for name in ("contract.md", "derivation.md", "validation.md"):
            self.assertTrue((TOP_DIR / name).is_file(), name)

    def test_derived_defaults_must_be_written_back(self):
        derivation = (TOP_DIR / "derivation.md").read_text(encoding="utf-8")
        self.assertIn("写回 top_input", derivation)
        self.assertIn("不读缺省", self.doc)
        for field in ("guard_root", "on_idle", "authorization", "deliverables.local_dir"):
            self.assertIn(field, derivation)

    def test_section_2_links_resolve(self):
        section = self.doc.split("## 2.", 1)[1].split("## 3.", 1)[0]
        links = re.findall(r"\]\(([^)#]+)\)", section)
        self.assertTrue(links, "section 2 should link to examples and playbooks")
        for target in links:
            if target.startswith("http"):
                continue
            self.assertTrue((TOP_DIR.parent / target).is_file(), target)


class TopInputExampleTest(unittest.TestCase):
    def setUp(self):
        self.doc = TOP_DOC.read_text(encoding="utf-8")
        self.paths = {path.name: path for path in EXAMPLES.glob("*.json")}

    def test_expected_examples_exist(self):
        self.assertEqual(set(self.paths), EXAMPLE_NAMES)

    def test_examples_are_listed_in_doc(self):
        for name in EXAMPLE_NAMES:
            self.assertIn(name, self.doc)

    def test_examples_have_no_placeholders(self):
        for name, path in self.paths.items():
            self.assertNotIn("{{", path.read_text(encoding="utf-8"), name)

    def test_examples_satisfy_invariants(self):
        for name, path in sorted(self.paths.items()):
            with self.subTest(example=name):
                check_example(json.loads(path.read_text(encoding="utf-8")))

    def test_examples_cover_topologies_and_actions(self):
        data = {name: json.loads(path.read_text(encoding="utf-8"))
                for name, path in self.paths.items()}
        deploy_only = data["colocated-existing-deploy-only.json"]
        self.assertEqual(deploy_only["task"]["action"], "deploy_only")
        self.assertEqual(deploy_only["task"]["on_idle"], "keep_service")
        self.assertNotIn("evaluation", deploy_only)
        self.assertIn("container", deploy_only["nodes"]["n1"])
        pd_only = data["pd-dual-host-existing-deploy-only.json"]
        self.assertEqual(pd_only["deployment"]["topology"], "pd")
        self.assertEqual(pd_only["task"]["on_idle"], "keep_service")
        self.assertEqual(len(pd_only["nodes"]), 2)
        self.assertTrue(all("container" in node for node in pd_only["nodes"].values()))
        self.assertIs(pd_only["nodes"]["n1"]["container"]["self"], True)
        self.assertNotIn("self", pd_only["nodes"]["n2"]["container"])
        self.assertIn("kv_transfer", pd_only["deployment"])
        existing_perf = data["colocated-existing-deploy-eval-perf.json"]
        self.assertEqual(existing_perf["deployment"]["topology"], "colocated")
        self.assertIn("container", existing_perf["nodes"]["n1"])
        self.assertIn("container", existing_perf["evaluation"]["tester"])
        existing_acc = data["colocated-existing-deploy-eval-acc.json"]
        self.assertEqual(existing_acc["task"]["eval_mode"], "acc")
        self.assertEqual(existing_acc["evaluation"]["dataset"]["id"], "gpqa")
        self.assertIs(existing_acc["nodes"]["n1"]["container"]["self"], True)
        eval_only = data["eval-only-external-acc.json"]
        self.assertEqual(eval_only["task"]["action"], "eval_only")
        self.assertIn("container", eval_only["evaluation"]["tester"])
        self.assertIn("container_work_root", eval_only["evaluation"]["tester"])
        owned = data["colocated-owned-deploy-eval-acc.json"]
        self.assertNotIn("container", owned["nodes"]["n1"])
        self.assertIn("image", owned["deployment"])

    def test_example_names_encode_scenario(self):
        for name, path in self.paths.items():
            with self.subTest(example=name):
                data = json.loads(path.read_text(encoding="utf-8"))
                task = data["task"]
                action = task["action"]
                if action == "deploy_and_eval":
                    self.assertIn("-deploy-eval-", name)
                    self.assertTrue(name.endswith("-" + task["eval_mode"] + ".json"), name)
                    self.assertTrue(name.startswith("colocated-"), name)
                    mode = ("existing" if any("container" in node for node in data["nodes"].values())
                            else "owned")
                    self.assertIn("-" + mode + "-", name)
                    self.assertNotIn("-self-", name)
                elif action == "deploy_only":
                    self.assertIn("-deploy-only.", name)
                elif action == "eval_only":
                    self.assertIn("eval-only-", name)
                else:
                    self.fail("unexpected action in example: " + action)



class ScenarioRouteTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        self.items = self.data["scenarios"]
        self.doc = TOP_DOC.read_text(encoding="utf-8")

    def test_routes_match_examples(self):
        self.assertEqual(self.data["schema_version"], "top-input-scenarios/1")
        ids = [item["id"] for item in self.items]
        self.assertEqual(len(ids), len(set(ids)), "duplicate scenario id")
        self.assertEqual(set(ids), {name[:-5] for name in EXAMPLE_NAMES})

    def test_every_route_has_existing_files(self):
        for item in self.items:
            with self.subTest(scenario=item["id"]):
                refs = ROUTER / "references"
                self.assertTrue((refs / item["example"]).is_file(), item["example"])
                self.assertTrue((refs / item["playbook"]).is_file(), item["playbook"])
                self.assertIn(item["id"], self.doc)

    def test_route_matches_example_content(self):
        for item in self.items:
            with self.subTest(scenario=item["id"]):
                example = json.loads((ROUTER / "references" / item["example"]).read_text(encoding="utf-8"))
                task = example["task"]
                self.assertEqual(item["action"], task["action"])
                self.assertEqual(item["eval_mode"], task.get("eval_mode"))
                if item["topology"]:
                    self.assertEqual(item["topology"], example["deployment"]["topology"])
                    expected_runtime = ("existing_container"
                                        if any("container" in node for node in example["nodes"].values())
                                        else "owned_container")
                    self.assertEqual(item.get("runtime"), expected_runtime)
                else:
                    self.assertNotIn("deployment", example)
                    self.assertIsNone(item.get("runtime"))
                self.assertNotIn("self", item, "self 是节点标记，不是场景属性")
                has_self = any(node.get("container", {}).get("self") for node in example["nodes"].values())
                if item.get("runtime") == "existing_container":
                    self.assertTrue(has_self, "existing 场景必须有一个 self 节点")
                self.assertEqual(validate_top_input(example)["scenario"], item["id"])
                self.assertTrue(item["required_facts"])
                self.assertTrue(item["cli"])

    def test_no_orphan_playbooks(self):
        referenced = {(ROUTER / "references" / item["playbook"]).resolve() for item in self.items}
        on_disk = {path.resolve() for path in PLAYBOOKS.glob("*.md")}
        self.assertEqual(on_disk, referenced)


class ContainerModeFixtureTest(unittest.TestCase):
    def setUp(self):
        self.data = {path.name: json.loads(path.read_text(encoding="utf-8"))
                     for path in EXAMPLES.glob("*.json")}

    def test_existing_examples_project_borrowed_container(self):
        for name, data in self.data.items():
            if data["task"]["action"] == "eval_only":
                continue
            if not any("container" in node for node in data["nodes"].values()):
                continue
            with self.subTest(example=name):
                self.assertNotIn("image", data["deployment"])
                for node in data["nodes"].values():
                    self.assertIn("container", node)
                    self.assertIn("container_work_root", node)
                    self.assertIn("network", node)

    def test_owned_example_lets_cli_create_container(self):
        for name, data in self.data.items():
            if data["task"]["action"] == "eval_only":
                continue
            if any("container" in node for node in data["nodes"].values()):
                continue
            with self.subTest(example=name):
                self.assertRegex(data["deployment"]["image"], r"^sha256:[0-9a-f]{64}$")
                tester = (data.get("evaluation") or {}).get("tester", {})
                self.assertNotIn("container", tester)
                self.assertNotIn("container_work_root", tester)

    def test_self_is_unique_and_controls_the_task(self):
        for name, data in self.data.items():
            self_nodes = [node_id for node_id, node in data["nodes"].items()
                          if node.get("container", {}).get("self")]
            existing = any("container" in node for node in data["nodes"].values())
            with self.subTest(example=name):
                if existing:
                    self.assertEqual(len(self_nodes), 1, "existing 必须恰好一个 self")
                    self.assertEqual(data["task"]["control_node"], self_nodes[0])
                else:
                    self.assertEqual(self_nodes, [], "owned 不得有 self")

    def test_eval_examples_carry_complete_tester(self):
        for name, data in self.data.items():
            if data["task"]["action"] not in EVAL_ACTIONS:
                continue
            with self.subTest(example=name):
                tester = data["evaluation"]["tester"]
                for key in ("host", "work_root", "python", "aisbench",
                            "model_path", "machine_id", "datasets"):
                    self.assertTrue(tester.get(key), key)
                if "container" in tester:
                    self.assertIn("container_work_root", tester)

    def test_dataset_only_for_acc(self):
        for name, data in self.data.items():
            if data["task"]["action"] not in EVAL_ACTIONS:
                continue
            with self.subTest(example=name):
                if data["task"]["eval_mode"] == "acc":
                    self.assertEqual(set(data["evaluation"]["dataset"]),
                                     {"id", "split", "scope"})
                else:
                    self.assertNotIn("dataset", data["evaluation"])

    def test_eval_only_has_service_and_no_deployment(self):
        for name, data in self.data.items():
            if data["task"]["action"] != "eval_only":
                continue
            with self.subTest(example=name):
                self.assertNotIn("deployment", data)
                self.assertNotIn("on_idle", data["task"])
                service = data["evaluation"]["service"]
                self.assertTrue(service.get("endpoint", "").startswith("http"))
                self.assertTrue(service.get("model"))


class SkillFrontmatterTest(unittest.TestCase):
    def test_name_matches_directory_and_description_exists(self):
        for skill in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
            with self.subTest(skill=skill.name):
                text = (skill / "SKILL.md").read_text(encoding="utf-8")
                name = re.search(r"^name:\s*(\S+)\s*$", text, re.M)
                self.assertIsNotNone(name, "missing frontmatter name")
                self.assertEqual(name.group(1), skill.name)
                self.assertRegex(text, r"(?m)^description:\s*\S+")

    def test_every_skill_points_to_installation_guide(self):
        guide = ROUTER / "references" / "installation.md"
        self.assertTrue(guide.is_file())
        body = guide.read_text(encoding="utf-8")
        self.assertIn("--version", body)
        self.assertIn("pip install", body)
        for name in sorted(path.name for path in SKILLS.iterdir() if path.is_dir()):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("installation.md", text)


class TopInputValidatorTest(unittest.TestCase):
    def setUp(self):
        source = (EXAMPLES / "colocated-existing-deploy-eval-acc.json").read_text(encoding="utf-8")
        self.example = json.loads(source)

    def codes(self, mutate):
        data = json.loads(json.dumps(self.example))
        mutate(data)
        return {issue["code"] for issue in validate_top_input(data)["issues"]}

    def test_self_node_must_be_control_node(self):
        codes = self.codes(lambda data: data["task"].update(control_node="n2"))
        self.assertIn("CONTROL_NODE", codes)
        self.assertIn("SELF_CONTROL_NODE", codes)

    def test_existing_requires_self_node(self):
        codes = self.codes(lambda data: data["nodes"]["n1"]["container"].pop("self"))
        self.assertIn("SELF_REQUIRED", codes)

    def test_self_value_must_be_true(self):
        codes = self.codes(lambda data: data["nodes"]["n1"]["container"].update(self=False))
        self.assertIn("SELF_VALUE", codes)

    def test_self_node_may_omit_image(self):
        data = json.loads(json.dumps(self.example))
        data["nodes"]["n1"]["container"].pop("image")
        codes = {issue["code"] for issue in validate_top_input(data)["issues"]}
        self.assertNotIn("CONTAINER_SHAPE", codes)
        self.assertNotIn("CONTAINER_IMAGE", codes)

    def test_non_self_node_must_keep_image(self):
        def mutate(data):
            data["nodes"]["n1"]["container"].pop("image")
            data["nodes"]["n1"]["container"].pop("self")
        self.assertIn("CONTAINER_SHAPE", self.codes(mutate))

    def test_tester_container_may_omit_image(self):
        data = json.loads(json.dumps(self.example))
        data["evaluation"]["tester"]["container"].pop("image")
        codes = {issue["code"] for issue in validate_top_input(data)["issues"]}
        self.assertNotIn("TESTER_CONTAINER", codes)

    def test_duplicate_self_node_is_rejected(self):
        data = json.loads(json.dumps(self.example))
        data["nodes"]["n1"]["container"]["self"] = True
        data["nodes"]["n2"] = json.loads(json.dumps(data["nodes"]["n1"]))
        data["nodes"]["n2"]["host"] = "example@192.0.2.39"
        data["nodes"]["n2"]["container"]["self"] = True
        data["nodes"]["n2"]["network"]["ip"] = "192.0.2.39"
        codes = {issue["code"] for issue in validate_top_input(data)["issues"]}
        self.assertIn("SELF_DUPLICATE", codes)

    def test_scenario_id_does_not_encode_self(self):
        report = validate_top_input(self.example)
        self.assertEqual(report["scenario"], "colocated-existing-deploy-eval-acc")
        self.assertNotIn("self", report["scenario"])

    def test_unknown_top_level_field_is_rejected(self):
        self.assertIn("UNKNOWN_FIELD", self.codes(lambda data: data.update(extra=1)))

    def test_owned_mode_rejects_container_work_root(self):
        def mutate(data):
            del data["nodes"]["n1"]["container"]
        self.assertIn("CONTAINER_MODE", self.codes(mutate))

    def test_reports_every_issue_not_just_first(self):
        def mutate(data):
            data["schema_version"] = "top-input/1"
            data["task"]["action"] = "bogus"
        codes = self.codes(mutate)
        self.assertIn("SCHEMA_VERSION", codes)
        self.assertIn("ACTION", codes)


class TopInputCompletenessTest(unittest.TestCase):
    """校验不读缺省：派单方可省的字段必须先写回具体值，缺失即缺口。"""

    def setUp(self):
        source = (EXAMPLES / "colocated-existing-deploy-eval-acc.json").read_text(encoding="utf-8")
        self.example = json.loads(source)

    def codes(self, mutate):
        data = json.loads(json.dumps(self.example))
        mutate(data)
        return {issue["code"] for issue in validate_top_input(data)["issues"]}

    def test_missing_guard_root_is_a_gap(self):
        self.assertIn("GUARD_ROOT", self.codes(lambda data: data["nodes"]["n1"].pop("guard_root")))

    def test_missing_node_python_is_a_gap(self):
        self.assertIn("NODE_PYTHON", self.codes(lambda data: data["nodes"]["n1"].pop("python")))

    def test_missing_node_host_is_a_gap(self):
        self.assertIn("HOST", self.codes(lambda data: data["nodes"]["n1"].pop("host")))

    def test_authorization_requires_both_keys(self):
        self.assertIn("AUTHORIZATION", self.codes(
            lambda data: data["nodes"]["n1"]["authorization"].pop("create_work_root")))

    def test_missing_on_idle_is_a_gap(self):
        self.assertIn("ON_IDLE", self.codes(lambda data: data["task"].pop("on_idle")))

    def test_missing_local_dir_is_a_gap(self):
        self.assertIn("LOCAL_DIR", self.codes(lambda data: data.pop("deliverables")))

    def test_local_dir_must_be_the_parent_of_task_root(self):
        def mutate(data):
            data["deliverables"]["local_dir"] += "/" + data["task"]["id"]
        self.assertIn("LOCAL_DIR", self.codes(mutate))

    def test_local_dir_must_not_live_under_allowed_root(self):
        self.assertIn("LOCAL_DIR_IN_ALLOWED_ROOT", self.codes(
            lambda data: data["deliverables"].update(
                local_dir=data["nodes"]["n1"]["allowed_root"] + "/deliverables")))

    def test_local_dir_must_not_equal_allowed_root(self):
        self.assertIn("LOCAL_DIR_IN_ALLOWED_ROOT", self.codes(
            lambda data: data["deliverables"].update(
                local_dir=data["nodes"]["n1"]["allowed_root"])))

    def test_local_dir_is_normalized_before_comparison(self):
        self.assertIn("LOCAL_DIR_IN_ALLOWED_ROOT", self.codes(
            lambda data: data["deliverables"].update(
                local_dir=data["nodes"]["n1"]["allowed_root"] + "/../work/deliverables")))

    def test_tester_machine_id_is_required(self):
        self.assertIn("TESTER_MACHINE_ID", self.codes(
            lambda data: data["evaluation"]["tester"].pop("machine_id")))

    def test_tester_machine_id_must_be_32_hex(self):
        self.assertIn("TESTER_MACHINE_ID", self.codes(
            lambda data: data["evaluation"]["tester"].update(machine_id="not-a-machine-id")))


if __name__ == "__main__":
    unittest.main()