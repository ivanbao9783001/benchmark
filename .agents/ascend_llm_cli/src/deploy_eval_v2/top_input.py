"""top_input 的业务校验。

规则与技能文档 `.agents/skills/ascend-llm-router/references/top-input/` 保持一致：
本模块是唯一实现，测试与 dispatch kit 都调用它，不在别处复制规则。
"""
import posixpath
import re

TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CONTAINER_ID = re.compile(r"[a-f0-9]{64}")
IMAGE_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")

ACTIONS = {"deploy_and_eval", "deploy_only", "eval_only", "release_only"}
DEPLOY_ACTIONS = {"deploy_and_eval", "deploy_only"}
EVAL_ACTIONS = {"deploy_and_eval", "eval_only"}
GENERATED_ARGS = {"host", "port", "served-model-name", "kv-transfer-config", "tensor-parallel-size"}
TOP_KEYS = {"schema_version", "task", "nodes", "deployment", "evaluation",
            "deliverables", "constraints"}
ISSUE_LIMIT = 32


def _issue(issues, code, path, message):
    issues.append({"code": code, "path": path, "message": message})


def _path(value):
    return isinstance(value, str) and value.startswith("/")


def _within(path, root):
    """path 等于 root 或位于 root 之下；规范化后再比较，避免 /a/../b 绕过。"""
    target = posixpath.normpath(path)
    base = posixpath.normpath(root)
    return target == base or target.startswith(base.rstrip("/") + "/")


def _container(node):
    return node.get("container") if isinstance(node, dict) else None


def scenario_id(data):
    """按结构推导场景 id，供 dispatch kit 选 playbook；不读取 skills 目录。"""
    task = data.get("task") or {}
    nodes = data.get("nodes") or {}
    action = task.get("action")
    if action == "release_only":
        return "release-only"
    if action == "eval_only":
        return "eval-only-external-" + str(task.get("eval_mode") or "?")
    deployment = data.get("deployment") or {}
    topology = str(deployment.get("topology") or "?")
    containers = [node.get("container") for node in nodes.values() if isinstance(node, dict)]
    parts = [topology]
    if topology == "pd":
        parts.append("dual-host" if len(nodes) > 1 else "single-host")
    parts.append("existing" if any(isinstance(item, dict) for item in containers) else "owned")
    parts.append("deploy-only" if action == "deploy_only" else "deploy-eval")
    if action == "deploy_and_eval" and task.get("eval_mode"):
        parts.append(task["eval_mode"])
    return "-".join(parts)


def validate(data):
    """返回 {'valid', 'scenario', 'issues'}；收集全部问题，不中断在第一条。"""
    issues = []
    if not isinstance(data, dict):
        return {"valid": False, "scenario": None, "issues": [
            {"code": "TOP_INPUT_NOT_OBJECT", "path": "", "message": "top_input 必须是 JSON 对象"}]}

    if data.get("schema_version") != "top-input/2":
        _issue(issues, "SCHEMA_VERSION", "schema_version", "必须是 top-input/2")
    for key in sorted(set(data) - TOP_KEYS):
        _issue(issues, "UNKNOWN_FIELD", key, "未知字段")

    task = data.get("task")
    if not isinstance(task, dict):
        _issue(issues, "TASK_REQUIRED", "task", "缺少 task 对象")
        task = {}
    action = task.get("action")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not TASK_ID.match(task_id):
        _issue(issues, "TASK_ID", "task.id", "必须是 1-64 位字母数字或 _-")
    if action not in ACTIONS:
        _issue(issues, "ACTION", "task.action", "必须是 " + "/".join(sorted(ACTIONS)))
    if action in DEPLOY_ACTIONS:
        if task.get("on_idle") not in {"release_owned", "keep_service"}:
            _issue(issues, "ON_IDLE", "task.on_idle", "部署动作必须给 release_owned 或 keep_service")
        if action == "deploy_only" and task.get("on_idle") != "keep_service":
            _issue(issues, "ON_IDLE", "task.on_idle", "deploy_only 只能是 keep_service")
    elif "on_idle" in task:
        _issue(issues, "ON_IDLE", "task.on_idle", "该 action 不允许 on_idle")
    if action in EVAL_ACTIONS and task.get("eval_mode") not in {"perf", "acc"}:
        _issue(issues, "EVAL_MODE", "task.eval_mode", "必须是 perf 或 acc")
    if action not in EVAL_ACTIONS and "eval_mode" in task:
        _issue(issues, "EVAL_MODE", "task.eval_mode", "该 action 不允许 eval_mode")

    nodes = data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        _issue(issues, "NODES_REQUIRED", "nodes", "至少一个节点")
        nodes = {}
    control = task.get("control_node")
    if not isinstance(control, str) or control not in nodes:
        _issue(issues, "CONTROL_NODE", "task.control_node", "必须是 nodes 的键")
    self_nodes = []
    has_existing = False
    for node_id, node in nodes.items():
        base = "nodes." + str(node_id)
        if not isinstance(node_id, str) or not NODE_ID.match(node_id):
            _issue(issues, "NODE_ID", base, "必须是 1-32 位字母数字或 _-")
        if not isinstance(node, dict):
            _issue(issues, "NODE_NOT_OBJECT", base, "必须是对象")
            continue
        if not isinstance(node.get("host"), str) or not node["host"]:
            _issue(issues, "HOST", base + ".host", "必填；self 节点填本机 hostname 或 IP")
        if not _path(node.get("allowed_root")):
            _issue(issues, "ALLOWED_ROOT", base + ".allowed_root", "必须是 POSIX 绝对路径")
        if action in DEPLOY_ACTIONS:
            if not _path(node.get("guard_root")):
                _issue(issues, "GUARD_ROOT", base + ".guard_root", "必须是 POSIX 绝对路径")
            if not _path(node.get("python")):
                _issue(issues, "NODE_PYTHON", base + ".python", "必须是 POSIX 绝对路径")
            auth = node.get("authorization")
            if not isinstance(auth, dict) or set(auth) != {"preempt_other_processes", "create_work_root"}:
                _issue(issues, "AUTHORIZATION", base + ".authorization",
                       "必须且只能有 preempt_other_processes、create_work_root")
            elif not all(isinstance(value, bool) for value in auth.values()):
                _issue(issues, "AUTHORIZATION", base + ".authorization", "两项都必须是布尔值")
            paths = node.get("model_paths")
            if not isinstance(paths, dict) or not paths:
                _issue(issues, "MODEL_PATHS", base + ".model_paths", "至少一个模型路径")
            else:
                for model, value in paths.items():
                    if not _path(value):
                        _issue(issues, "MODEL_PATHS", base + ".model_paths." + str(model),
                               "必须是 POSIX 绝对路径")
        container = node.get("container")
        if container is None:
            if "container_work_root" in node:
                _issue(issues, "CONTAINER_MODE", base + ".container_work_root",
                       "owned 模式不允许 container_work_root")
            continue
        has_existing = True
        is_self = isinstance(container, dict) and container.get("self") is True
        required = {"id"} if is_self else {"id", "image"}
        if (not isinstance(container, dict)
                or not required <= set(container) <= {"id", "image", "self", "device_id_map"}):
            _issue(issues, "CONTAINER_SHAPE", base + ".container",
                   "self 节点必须且只能有 id、可选 image、self；其它节点必须且只能有 id、image、可选 self；借用容器可另带 device_id_map")
            continue
        mapping = container.get("device_id_map")
        if mapping is not None:
            if (not isinstance(mapping, dict) or not mapping
                    or any(not isinstance(key, str) or not re.fullmatch(r"0|[1-9][0-9]*", key)
                           or type(value) is not int or value < 0
                           for key, value in mapping.items())
                    or sorted(mapping.values()) != list(range(len(mapping)))):
                _issue(issues, "DEVICE_ID_MAP", base + ".container.device_id_map",
                       "必须是 {宿主物理NPU号: 容器逻辑号}，逻辑号从 0 连续且唯一")
        if not isinstance(container.get("id"), str) or not CONTAINER_ID.fullmatch(container["id"]):
            _issue(issues, "CONTAINER_ID", base + ".container.id", "必须是 64 位小写十六进制")
        if "image" in container and (not isinstance(container.get("image"), str)
                                     or not IMAGE_DIGEST.fullmatch(container["image"])):
            _issue(issues, "CONTAINER_IMAGE", base + ".container.image", "必须是 sha256: + 64 位小写十六进制")
        work_root = node.get("container_work_root")
        if not _path(work_root):
            _issue(issues, "CONTAINER_WORK_ROOT", base + ".container_work_root", "必须是 POSIX 绝对路径")
        elif ":" in work_root or ".." in work_root.split("/"):
            _issue(issues, "CONTAINER_WORK_ROOT", base + ".container_work_root", "不得含 : 或 ..")
        if not node.get("network"):
            _issue(issues, "NETWORK_REQUIRED", base + ".network", "借用容器必须给 network")
        if "self" in container:
            if container["self"] is not True:
                _issue(issues, "SELF_VALUE", base + ".container.self", "只能写 true")
            if node_id != control:
                _issue(issues, "SELF_CONTROL_NODE", base + ".container.self", "只有控制节点可以 self")
            self_nodes.append(node_id)
    if len(self_nodes) > 1:
        _issue(issues, "SELF_DUPLICATE", "nodes", "全任务必须恰好一个 container.self")
    elif has_existing and not self_nodes:
        _issue(issues, "SELF_REQUIRED", "nodes",
               "existing 模式必须有一个 container.self=true 表示当前所在容器")

    hosts = [node.get("host", "").split("@")[-1] for node in nodes.values() if isinstance(node, dict)]
    if len(hosts) != len(set(hosts)):
        _issue(issues, "DUPLICATE_HOST", "nodes", "节点 host 去掉 user 后必须互不相同")

    if action in DEPLOY_ACTIONS:
        _validate_deployment(data, task, nodes, issues)
    elif "deployment" in data:
        _issue(issues, "DEPLOYMENT_FORBIDDEN", "deployment", "该 action 不允许 deployment")

    if action in EVAL_ACTIONS:
        _validate_evaluation(data, task, nodes, issues)
    elif "evaluation" in data:
        _issue(issues, "EVALUATION_FORBIDDEN", "evaluation", "该 action 不允许 evaluation")

    deliverables = data.get("deliverables")
    local_dir = deliverables.get("local_dir") if isinstance(deliverables, dict) else None
    if not _path(local_dir):
        _issue(issues, "LOCAL_DIR", "deliverables.local_dir", "必须是 POSIX 绝对路径")
    elif isinstance(task_id, str) and local_dir.rstrip("/").endswith("/" + task_id):
        _issue(issues, "LOCAL_DIR", "deliverables.local_dir", "必须是父目录，不含 task.id")
    if _path(local_dir):
        for node in nodes.values():
            root = node.get("allowed_root") if isinstance(node, dict) else None
            if _path(root) and _within(local_dir, root):
                _issue(issues, "LOCAL_DIR_IN_ALLOWED_ROOT", "deliverables.local_dir",
                       "不得等于或位于 allowed_root 内；allowed_root 只承载节点运行根")
                break
    if not isinstance(data.get("constraints"), list):
        _issue(issues, "CONSTRAINTS", "constraints", "必须是数组")

    report = {"valid": not issues, "scenario": scenario_id(data), "issues": issues[:ISSUE_LIMIT]}
    if len(issues) > ISSUE_LIMIT:
        report["issues"].append({"code": "MORE_ISSUES", "path": "",
                                 "message": "另有 %d 条未列出" % (len(issues) - ISSUE_LIMIT)})
    return report


def _validate_deployment(data, task, nodes, issues):
    dep = data.get("deployment")
    action = task.get("action")
    if not isinstance(dep, dict):
        _issue(issues, "DEPLOYMENT_REQUIRED", "deployment", "缺少 deployment 对象")
        return
    if dep.get("id") != task.get("id"):
        _issue(issues, "DEPLOYMENT_ID", "deployment.id", "必须等于 task.id")
    topology = dep.get("topology")
    if topology not in {"colocated", "pd"}:
        _issue(issues, "TOPOLOGY", "deployment.topology", "必须是 colocated 或 pd")
    has_container = any(_container(node) for node in nodes.values() if isinstance(node, dict))
    if has_container:
        if "image" in dep:
            _issue(issues, "DEPLOYMENT_IMAGE", "deployment.image", "借用容器时不得写 deployment.image")
    elif not IMAGE_DIGEST.fullmatch(str(dep.get("image", ""))):
        _issue(issues, "DEPLOYMENT_IMAGE", "deployment.image", "owned 模式必须给镜像摘要")
    model = dep.get("model")
    if not isinstance(model, dict) or not MODEL_ID.match(str(model.get("id", ""))):
        _issue(issues, "DEPLOYMENT_MODEL", "deployment.model.id", "必须是 1-64 位字母数字或 _-")
        model = {}
    instances = dep.get("instances")
    if not isinstance(instances, list) or not instances:
        _issue(issues, "INSTANCES_REQUIRED", "deployment.instances", "至少一个实例")
        return
    ids = [item.get("id") for item in instances if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        _issue(issues, "INSTANCE_ID", "deployment.instances", "实例 id 必须唯一")
    devices_by_node, ports_by_node = {}, {}
    widths = set()
    for index, item in enumerate(instances):
        base = "deployment.instances[%d]" % index
        if not isinstance(item, dict):
            _issue(issues, "INSTANCE_NOT_OBJECT", base, "必须是对象")
            continue
        node_id = item.get("node")
        if not isinstance(node_id, str) or not isinstance(nodes.get(node_id), dict):
            _issue(issues, "INSTANCE_NODE", base + ".node", "必须是 nodes 的键")
            continue
        ports = item.get("ports")
        if not isinstance(ports, dict) or not ports:
            _issue(issues, "INSTANCE_PORTS", base + ".ports", "至少一个端口")
            ports = {}
        for value in ports.values():
            if type(value) is not int or not 1 <= value <= 65535:
                _issue(issues, "INSTANCE_PORTS", base + ".ports", "端口必须是 1-65535 的整数")
            else:
                ports_by_node.setdefault(node_id, []).append(value)
        if set(item.get("args") or {}) & GENERATED_ARGS:
            _issue(issues, "INSTANCE_ARGS", base + ".args", "不得覆盖由结构派生的参数")
        role = item.get("role")
        if role == "proxy":
            if item.get("devices"):
                _issue(issues, "PROXY_DEVICES", base + ".devices", "proxy 不得有 devices")
            if set(ports) != {"http"}:
                _issue(issues, "PROXY_PORTS", base + ".ports", "proxy 只能有 http")
            continue
        if role not in {"mixed", "prefill", "decode"}:
            _issue(issues, "INSTANCE_ROLE", base + ".role", "必须是 mixed/prefill/decode/proxy")
            continue
        devices = item.get("devices")
        if not isinstance(devices, list) or not devices or any(
                type(value) is not int or value < 0 for value in devices):
            _issue(issues, "INSTANCE_DEVICES", base + ".devices", "必须是非负整数数组")
        else:
            if len(devices) != len(set(devices)):
                _issue(issues, "INSTANCE_DEVICES", base + ".devices", "设备号不得重复")
            devices_by_node.setdefault(node_id, []).extend(devices)
            widths.add(len(devices))
            if model.get("id") and model["id"] not in (nodes[node_id].get("model_paths") or {}):
                _issue(issues, "MODEL_PATH_MISSING", base + ".node",
                       "该节点缺少 model_paths[%s]" % model["id"])
        expected = {"http"} if role == "mixed" else {"http", "kv"}
        if set(ports) != expected:
            _issue(issues, "INSTANCE_PORTS", base + ".ports", "端口名集应为 " + "/".join(sorted(expected)))
    used = {item.get("node") for item in instances if isinstance(item, dict)}
    for node_id in nodes:
        if node_id not in used and node_id != task.get("control_node"):
            _issue(issues, "NODE_UNUSED", "nodes." + str(node_id), "未被任何实例使用，也不是控制节点")
    for node_id, values in devices_by_node.items():
        if len(values) != len(set(values)):
            _issue(issues, "DEVICE_OVERLAP", "nodes." + str(node_id) + ".devices", "同节点设备不得重叠")
    for node_id, values in ports_by_node.items():
        if len(values) != len(set(values)):
            _issue(issues, "PORT_OVERLAP", "nodes." + str(node_id) + ".ports", "同节点端口不得重叠")
    endpoint = dep.get("endpoint")
    by_id = {item.get("id"): item for item in instances if isinstance(item, dict)}
    target = endpoint.get("instance") if isinstance(endpoint, dict) else None
    if not isinstance(endpoint, dict) or not isinstance(target, str) or target not in by_id:
        _issue(issues, "ENDPOINT", "deployment.endpoint", "必须指向已存在实例")
    elif not isinstance(endpoint.get("port"), str) or endpoint["port"] not in (by_id[target].get("ports") or {}):
        _issue(issues, "ENDPOINT", "deployment.endpoint.port", "必须是该实例已声明的端口名")
    elif topology == "colocated" and by_id[target].get("role") != "mixed":
        _issue(issues, "ENDPOINT", "deployment.endpoint", "colocated 必须指向 mixed")
    elif topology == "pd" and by_id[target].get("role") != "proxy":
        _issue(issues, "ENDPOINT", "deployment.endpoint", "pd 必须指向 proxy")
    if topology == "colocated":
        if len(instances) != 1 or instances[0].get("role") != "mixed":
            _issue(issues, "TOPOLOGY", "deployment.instances", "colocated 恰好一个 mixed")
        if "kv_transfer" in dep:
            _issue(issues, "KV_FORBIDDEN", "deployment.kv_transfer", "colocated 不允许 kv_transfer")
    elif topology == "pd":
        roles = [item.get("role") for item in instances if isinstance(item, dict)]
        if "mixed" in roles:
            _issue(issues, "TOPOLOGY", "deployment.instances", "pd 不允许 mixed")
        if roles.count("proxy") != 1 or not {"prefill", "decode"} <= set(roles):
            _issue(issues, "TOPOLOGY", "deployment.instances", "pd 需要 prefill、decode 和恰好一个 proxy")
        kv = dep.get("kv_transfer")
        if not isinstance(kv, dict) or not kv.get("connector"):
            _issue(issues, "KV_REQUIRED", "deployment.kv_transfer.connector", "pd 必须给 connector")
        if len(widths) > 1:
            _issue(issues, "KV_SIGNATURE", "deployment.instances", "prefill/decode 设备数必须相同")
        for item in instances:
            if isinstance(item, dict) and item.get("role") == "proxy":
                node = nodes.get(item.get("node")) or {}
                if "proxy_program" not in (node.get("paths") or {}):
                    _issue(issues, "PROXY_PROGRAM", "nodes." + str(item.get("node")) + ".paths",
                           "proxy 所在节点必须有 proxy_program")


def _validate_evaluation(data, task, nodes, issues):
    evaluation = data.get("evaluation")
    if not isinstance(evaluation, dict):
        _issue(issues, "EVALUATION_REQUIRED", "evaluation", "缺少 evaluation 对象")
        return
    tester = evaluation.get("tester")
    if not isinstance(tester, dict):
        _issue(issues, "TESTER_REQUIRED", "evaluation.tester", "缺少 tester 对象")
        return
    for key in ("host", "work_root", "python", "aisbench", "model_path", "datasets"):
        if not tester.get(key):
            _issue(issues, "TESTER_FIELD", "evaluation.tester." + key, "必填")
    if not _path(tester.get("work_root")):
        _issue(issues, "TESTER_FIELD", "evaluation.tester.work_root", "必须是 POSIX 绝对路径")
    if not _path(tester.get("model_path")):
        _issue(issues, "TESTER_FIELD", "evaluation.tester.model_path", "必须是 POSIX 绝对路径")
    if not isinstance(tester.get("machine_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", tester["machine_id"]):
        _issue(issues, "TESTER_MACHINE_ID", "evaluation.tester.machine_id",
               "必须是 32 位小写十六进制机器码，现场取证后写回")
    if "host_python" in tester and not _path(tester["host_python"]):
        _issue(issues, "TESTER_FIELD", "evaluation.tester.host_python", "必须是 POSIX 绝对路径")
    datasets = tester.get("datasets")
    if not isinstance(datasets, dict) or not datasets:
        _issue(issues, "TESTER_DATASETS", "evaluation.tester.datasets", "至少一个数据集")
        datasets = {}
    container = tester.get("container")
    if container is not None:
        if (not isinstance(container, dict)
                or not {"id"} <= set(container) <= {"id", "image"}):
            _issue(issues, "TESTER_CONTAINER", "evaluation.tester.container", "必须包含 id，可选 image")
        else:
            if not CONTAINER_ID.fullmatch(str(container.get("id", ""))):
                _issue(issues, "TESTER_CONTAINER", "evaluation.tester.container.id", "必须是 64 位小写十六进制")
            if "image" in container and not IMAGE_DIGEST.fullmatch(str(container.get("image", ""))):
                _issue(issues, "TESTER_CONTAINER", "evaluation.tester.container.image", "必须是镜像摘要")
        if not _path(tester.get("container_work_root")):
            _issue(issues, "TESTER_WORK_ROOT", "evaluation.tester.container_work_root", "必须是 POSIX 绝对路径")
    elif "container_work_root" in tester:
        _issue(issues, "TESTER_CONTAINER", "evaluation.tester.container_work_root", "缺少 container")
    if task.get("eval_mode") == "acc":
        dataset = evaluation.get("dataset")
        if not isinstance(dataset, dict) or set(dataset) != {"id", "split", "scope"}:
            _issue(issues, "DATASET_REQUIRED", "evaluation.dataset", "acc 必须给 id/split/scope")
        elif not isinstance(dataset.get("id"), str) or dataset["id"] not in datasets:
            _issue(issues, "DATASET_UNKNOWN", "evaluation.dataset.id", "必须在 tester.datasets 中")
    elif "dataset" in evaluation:
        _issue(issues, "DATASET_FORBIDDEN", "evaluation.dataset", "perf 不允许 dataset")
    if "params" in evaluation and not isinstance(evaluation["params"], dict):
        _issue(issues, "EVAL_PARAMS", "evaluation.params", "必须是对象")
    if task.get("action") == "eval_only":
        service = evaluation.get("service")
        if not isinstance(service, dict):
            _issue(issues, "SERVICE_REQUIRED", "evaluation.service", "eval_only 必须给 service")
        else:
            if not str(service.get("endpoint", "")).startswith("http"):
                _issue(issues, "SERVICE_ENDPOINT", "evaluation.service.endpoint", "必须是 http(s) URL")
            if not service.get("model"):
                _issue(issues, "SERVICE_MODEL", "evaluation.service.model", "必填")
        control = task.get("control_node")
        control_host = (nodes.get(control) or {}).get("host") if isinstance(control, str) else None
        if control_host and tester.get("host") != control_host:
            _issue(issues, "EVAL_ONLY_HOST", "evaluation.tester.host", "必须等于控制节点 host")
    elif "service" in evaluation:
        _issue(issues, "SERVICE_FORBIDDEN", "evaluation.service", "该 action 不允许 service")
