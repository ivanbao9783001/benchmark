import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

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
EXAMPLES = ROUTER / "references" / "examples"
EXAMPLE_NAMES = {
    "colocated-existing-deploy-eval-perf.json",
    "colocated-existing-deploy-eval-acc.json",
    "colocated-existing-deploy-only.json",
    "pd-dual-host-existing-deploy-only.json",
    "eval-only-external-acc.json",
}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
DEPLOY_ACTIONS = {"deploy_and_eval", "deploy_only"}
EVAL_ACTIONS = {"deploy_and_eval", "eval_only"}
GENERATED_ARGS = {"host", "port", "served-model-name", "kv-transfer-config", "tensor-parallel-size"}
PARAM_KEYS = {"concurrency", "requests", "input_tokens", "output_tokens",
              "num_warmups", "timeout_seconds", "max_out_len"}


def check_example(data):
    """Shared invariants for filled top_input; mirrors the documented rules."""
    assert data["schema_version"] == "top-input/2"
    assert set(data) <= {"schema_version", "task", "nodes", "deployment",
                         "evaluation", "deliverables", "constraints"}, sorted(data)
    task = data["task"]
    action, task_id = task["action"], task["id"]
    assert ID.match(task_id), task_id
    assert action in {"deploy_and_eval", "deploy_only", "eval_only", "release_only"}
    if action in DEPLOY_ACTIONS:
        assert task.get("on_idle") in {"release_owned", "keep_service"}
        if action == "deploy_only":
            assert task["on_idle"] == "keep_service"
    else:
        assert "on_idle" not in task
    if action in EVAL_ACTIONS:
        assert task.get("eval_mode") in {"perf", "acc"}
    nodes = data.get("nodes", {})
    assert nodes, "nodes required"
    assert task.get("control_node") in nodes, "control_node must be a node key"
    for node_id, node in nodes.items():
        assert ID.match(node_id), node_id
        assert node["allowed_root"].startswith("/")
        if action in DEPLOY_ACTIONS:
            assert node["guard_root"].startswith("/")
            assert node["python"].startswith("/")
            assert set(node["authorization"]) == {"preempt_other_processes", "create_work_root"}
            assert isinstance(node["authorization"]["preempt_other_processes"], bool)
        for value in node.get("model_paths", {}).values():
            assert value.startswith("/")
        if "container" in node:
            assert set(node["container"]) == {"id", "image"}
            assert re.fullmatch(r"[a-f0-9]{64}", node["container"]["id"]), node["container"]["id"]
            assert re.fullmatch(r"sha256:[a-f0-9]{64}", node["container"]["image"]), node["container"]["image"]
            assert node["container_work_root"].startswith("/")
            assert ":" not in node["container_work_root"]
            assert node.get("network"), "existing container requires network"
        else:
            assert "container_work_root" not in node
    hosts = [node["host"].split("@")[-1] for node in nodes.values()]
    assert len(hosts) == len(set(hosts)), "duplicate host"
    if action in EVAL_ACTIONS:
        tester_host = data["evaluation"]["tester"]["host"].split("@")[-1]
        if action == "eval_only":
            control_host = nodes[task["control_node"]]["host"].split("@")[-1]
            assert tester_host == control_host, "eval_only tester must run on the control node"
        hosts.append(tester_host)
    for host in hosts:
        assert host.startswith("192.0.2."), "example host must be illustrative: " + host

    if action in DEPLOY_ACTIONS:
        dep = data["deployment"]
        assert dep["id"] == task_id
        assert dep["topology"] in {"colocated", "pd"}
        if "image" in dep:
            assert re.fullmatch(r"sha256:[a-f0-9]{64}", dep["image"])
        instances = dep["instances"]
        ids = [item["id"] for item in instances]
        assert len(ids) == len(set(ids))
        for item in instances:
            assert item["node"] in nodes, item["id"]
        assert {item["node"] for item in instances} <= set(nodes)
        assert set(nodes) - {item["node"] for item in instances} <= {task["control_node"]}
        devices_by_node, ports_by_node = {}, {}
        for item in instances:
            ports = item["ports"]
            assert ports and all(isinstance(v, int) and 1 <= v <= 65535 for v in ports.values())
            ports_by_node.setdefault(item["node"], []).extend(ports.values())
            assert not (set(item.get("args") or {}) & GENERATED_ARGS), item["id"]
            if item["role"] == "proxy":
                assert not item.get("devices")
                assert set(ports) == {"http"}
                continue
            assert item["role"] in {"mixed", "prefill", "decode"}
            devices = item["devices"]
            assert devices and all(isinstance(d, int) and d >= 0 for d in devices)
            assert len(devices) == len(set(devices))
            assert set(ports) == ({"http"} if item["role"] == "mixed" else {"http", "kv"})
            devices_by_node.setdefault(item["node"], []).extend(devices)
            assert dep["model"]["id"] in nodes[item["node"]]["model_paths"]
        for values in devices_by_node.values():
            assert len(values) == len(set(values)), "overlapping devices"
        for values in ports_by_node.values():
            assert len(values) == len(set(values)), "overlapping ports"
        endpoint = dep["endpoint"]
        by_id = {item["id"]: item for item in instances}
        assert endpoint["instance"] in by_id
        assert endpoint["port"] in by_id[endpoint["instance"]]["ports"]
        if dep["topology"] == "colocated":
            assert len(instances) == 1 and instances[0]["role"] == "mixed"
            assert "kv_transfer" not in dep
        else:
            roles = [item["role"] for item in instances]
            assert roles.count("proxy") == 1
            assert {"prefill", "decode"} <= set(roles) and "mixed" not in roles
            assert dep["kv_transfer"]["connector"]
            assert by_id[endpoint["instance"]]["role"] == "proxy"
            proxy_node = nodes[by_id[endpoint["instance"]]["node"]]
            assert "proxy_program" in (proxy_node.get("paths") or {})
            widths = {len(item["devices"]) for item in instances if item["role"] != "proxy"}
            assert len(widths) == 1, "PD prefill/decode device counts differ"

    if action in EVAL_ACTIONS:
        evaluation = data["evaluation"]
        tester = evaluation["tester"]
        for key in ("host", "work_root", "python", "aisbench", "model_path", "datasets"):
            assert key in tester, key
        if "container" in tester:
            assert set(tester["container"]) == {"id", "image"}
            assert tester["container_work_root"].startswith("/")
            assert ":" not in tester["container_work_root"]
        if "host_python" in tester:
            assert tester["host_python"].startswith("/")
        assert tester["datasets"] and isinstance(tester["datasets"], dict)
        if task["eval_mode"] == "acc":
            assert set(evaluation.get("dataset") or {}) == {"id", "split", "scope"}
        else:
            assert evaluation.get("dataset") is None
        if evaluation.get("params"):
            assert set(evaluation["params"]) <= PARAM_KEYS
        if action == "eval_only":
            assert "deployment" not in data
            assert evaluation["service"]["endpoint"].startswith("http")
            assert evaluation["service"]["model"]
        else:
            assert "service" not in evaluation
    else:
        assert "evaluation" not in data
    if action in {"eval_only", "release_only"}:
        assert "deployment" not in data

    local_dir = data["deliverables"]["local_dir"]
    assert local_dir and not local_dir.rstrip("/").endswith("/" + task_id)


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
        for text in ("单机 PD", "双机 PD", "proxy_program", "evaluation.params"):
            self.assertIn(text, self.doc)
        self.assertIn("只照抄结构", self.doc)


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
        self.assertIn("kv_transfer", pd_only["deployment"])
        existing_perf = data["colocated-existing-deploy-eval-perf.json"]
        self.assertEqual(existing_perf["deployment"]["topology"], "colocated")
        self.assertIn("container", existing_perf["nodes"]["n1"])
        self.assertIn("container", existing_perf["evaluation"]["tester"])
        existing_acc = data["colocated-existing-deploy-eval-acc.json"]
        self.assertEqual(existing_acc["task"]["eval_mode"], "acc")
        self.assertEqual(existing_acc["evaluation"]["dataset"]["id"], "gpqa")
        eval_only = data["eval-only-external-acc.json"]
        self.assertEqual(eval_only["task"]["action"], "eval_only")
        self.assertIn("container", eval_only["evaluation"]["tester"])
        self.assertIn("container_work_root", eval_only["evaluation"]["tester"])

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
                elif action == "deploy_only":
                    self.assertIn("-deploy-only.", name)
                elif action == "eval_only":
                    self.assertIn("eval-only-", name)
                else:
                    self.fail("unexpected action in example: " + action)



if __name__ == "__main__":
    unittest.main()