# 真实需求 Precommit 样板复跑模板

适用场景：业务代码已经由人工或 Codex 改在本地仓库中，需要用 Harness 对当前 diff 做提交前验证，并把产物登记进 Task Manager。

## 1. 运行 precommit 验证

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path <业务仓库绝对路径> \
  --allowed-path <本需求允许修改的相对路径> \
  --verify-command '<在临时 worktree 中执行的验证命令>' \
  --title "<需求标题>" \
  --entity-id <DFHIS-ID> \
  --demand-text "<需求口径、菜单参数、边界和人工验收说明>" \
  --method-test-command '<可选：输出 method cases JSON 的命令>' \
  --ui-capture-command '<可选：输出 UI artifacts/assertions JSON 的命令>' \
  --worktree-dir /tmp/his_harness_<DFHIS-ID>_worktrees \
  --output-dir /tmp/his_harness_<DFHIS-ID>_precommit
```

如使用人工 UI 证据，也可以改用：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path <业务仓库绝对路径> \
  --allowed-path <本需求允许修改的相对路径> \
  --verify-command '<验证命令>' \
  --title "<需求标题>" \
  --entity-id <DFHIS-ID> \
  --demand-text "<需求口径>" \
  --ui-evidence-path <人工验收记录或截图路径> \
  --worktree-dir /tmp/his_harness_<DFHIS-ID>_worktrees \
  --output-dir /tmp/his_harness_<DFHIS-ID>_precommit
```

## 2. 登记进 Task Manager

```bash
python3 tools/task_manager.py register-run \
  --yunxiao-url "<云效需求或缺陷链接>" \
  --title "<需求标题>" \
  --entity-kind requirement \
  --entity-id <DFHIS-ID> \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path <业务仓库绝对路径> \
  --output-dir /tmp/his_harness_<DFHIS-ID>_precommit \
  --execution-mode precommit-verify \
  --notes "register real precommit trial"
```

登记后检查：

```bash
python3 tools/task_manager.py show --task-key requirement-<dfhis-id小写>
```

## 3. 从 Task Manager 复跑 precommit

登记后可直接从任务记录复跑。命令行显式参数优先，任务记录中的项目路径作为兜底：

```bash
python3 tools/task_manager.py rerun-precommit \
  --task-key requirement-<dfhis-id小写> \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path <业务仓库绝对路径> \
  --allowed-path <本需求允许修改的相对路径> \
  --verify-command '<验证命令>' \
  --demand "<需求口径>" \
  --output-root /tmp/his_harness_tasks \
  --worktree-dir /tmp/his_harness_task_worktrees
```

复跑会自动生成并登记：

- `task_manager_real_trial_record.json/md`
- `task_manager_run_history.json/md`
- `ui_evidence_reuse_policy.json/md`

同一个 `task_id + output_dir + execution_mode` 重复 `register-run` 时会复用原 `task_run/run_id`，不会重复登记。

## 4. DFHIS-31465 已登记样板

```bash
python3 tools/task_manager.py show --task-key requirement-dfhis-31465
```

当前样板：

- Task ID：2
- Run ID：325
- Output Dir：`/tmp/his_harness_DFHIS-31465_v0104_trial`
- 登记记录：`/tmp/his_harness_DFHIS-31465_v0104_trial/task_manager_real_trial_record.md`
