import { api } from "../api/workbench.js";
import { errorText, field } from "./common/fields.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";

const STATUS_CLASS = {
  pending: "state-pending",
  released: "state-released",
  delivered: "state-done",
  failed: "state-failed",
  restored: "state-restored",
};

function shortageChip(batch) {
  if (!batch.shortage || batch.shortage <= 0) {
    return el("span", {class: "margin-ok", "data-testid": "margin-ok"}, "余量足够");
  }
  return el("span", {
    class: "shortage",
    "data-testid": "shortage",
    "data-shortage": String(batch.shortage),
  }, `还缺${batch.shortage}台`);
}

function statusBadge(batch) {
  let text = batch.status_label || batch.status;
  if (batch.status === "pending") {
    text = batch.shortage > 0 ? "暂不放行" : "待放行";
  }
  return el("span", {
    class: `release-status status ${STATUS_CLASS[batch.status] || ""}`,
    "data-testid": "release-status",
    "data-status": batch.status,
    "data-release-status": batch.release_status,
  }, text);
}

function actionButton(label, testid, attrs, onClick) {
  return el("button", {
    type: "button", class: attrs.class || "quiet",
    "data-testid": testid, ...attrs.data,
    onclick: event => onClick(event.currentTarget),
  }, label);
}

export function retirementBoard(user) {
  const canAct = user && user.role !== "viewer";
  const feedback = el("p", {class: "board-feedback", "data-testid": "board-feedback", role: "status"});
  const body = el("div", {class: "board-body"}, el("p", {class: "empty"}, "正在载入放行台账…"));
  const root = el("section", {
    class: "panel release-board", id: "release-board", "data-testid": "release-board",
  },
    el("div", {class: "release-board-head"},
      el("h2", {}, "炉体退役批次 · 放行状态"),
      el("p", {class: "rule", "data-testid": "release-rule"},
        "办退役时，每条产线都得留一台可用烘箱。已放行的旧炉在交卷前也不算可用；替换炉验收后才计入可用数，验收重试不重复计数；交卷失败后，须由设备组确认旧炉恢复可用才计回，其他产线照常办理。")),
    feedback,
    body,
  );

  function notify(message, tone = "info") {
    feedback.textContent = message || "";
    feedback.dataset.tone = tone;
    if (message) {
      feedback.setAttribute("role", tone === "error" ? "alert" : "status");
    }
  }

  async function call(button, fn, okMessage) {
    if (button) button.disabled = true;
    notify("处理中…");
    try {
      const result = await fn();
      notify(typeof okMessage === "function" ? okMessage(result) : okMessage, "ok");
      await paint();
      return result;
    } catch (error) {
      notify(error.message || "操作失败", "error");
      if (button) button.disabled = false;
    }
  }

  function batchActions(batch) {
    if (!canAct) return el("span", {class: "readonly-hint"}, "只读账号");
    const wrap = el("div", {class: "row-actions"});
    if (batch.status === "pending") {
      wrap.append(
        actionButton("放行", "release-batch",
          {class: "primary", data: {"data-batch-code": batch.code}},
          btn => call(btn, () => api.releaseBatch(batch.id), r =>
            r.released ? `已放行 ${batch.code}，该批旧炉即刻移出可用数。`
              : `${batch.code} 未放行：${r.batch?.shortage_text || `还缺${r.shortage}台`}。`)),
        actionButton("重新判定", "rejudge-batch",
          {data: {"data-batch-code": batch.code}},
          btn => call(btn, () => api.rejudgeBatch(batch.id), r =>
            r.released ? `重新判定通过，${batch.code} 已放行。`
              : `重新判定：${r.batch?.shortage_text || `还缺${r.shortage}台`}，补足后再判。`)),
      );
    } else if (batch.status === "released") {
      wrap.append(
        actionButton("交卷", "submit-batch", {class: "primary", data: {"data-batch-code": batch.code}},
          btn => call(btn, () => api.submitReleaseBatch(batch.id, {}), () => `${batch.code} 已交卷。`)),
        actionButton("交卷失败", "fail-batch", {class: "danger", data: {"data-batch-code": batch.code}},
          btn => call(btn, () => api.failReleaseBatch(batch.id, {}), () =>
            `${batch.code} 交卷失败：旧炉仍不计入可用，等待设备组确认恢复。`)),
      );
    } else if (batch.status === "failed") {
      wrap.append(actionButton("设备组确认恢复可用", "confirm-restore-batch",
        {class: "primary", data: {"data-batch-code": batch.code}},
        btn => call(btn, () => api.restoreBatch(batch.id), r =>
          r.batch?.status === "restored"
            ? `已确认 ${batch.code} 的旧炉恢复可用并计回，缺口重新判定。`
            : "已确认部分旧炉恢复，全部恢复后该批收尾。")));
    } else {
      wrap.append(el("span", {class: "done-hint"}, batch.status_label));
    }
    return wrap;
  }

  function batchTable(batches, testid, label) {
    const rows = batches.map(batch => el("tr", {
      "data-testid": "batch-row",
      "data-batch-id": String(batch.id),
      "data-batch-code": batch.code,
      "data-line-code": batch.line_code || "",
      "data-status": batch.status,
    },
      el("td", {class: "cell-code"}, el("strong", {}, batch.code),
        batch.title ? el("small", {}, batch.title) : null),
      el("td", {}, batch.line_name ? `${batch.line_name} ` : "",
        batch.line_code ? el("span", {class: "code-tag"}, batch.line_code) : null),
      el("td", {}, (batch.oven_codes || []).length ? batch.oven_codes.join("、") : `申请 ${batch.retire_count} 台`),
      el("td", {}, statusBadge(batch)),
      el("td", {class: "cell-shortage"}, shortageChip(batch),
        el("small", {class: "verdict-text"}, batch.verdict_text || "")),
      el("td", {class: "cell-actions"}, batchActions(batch)),
    ));
    return el("div", {class: "table-scroll", tabindex: "0", role: "region", "aria-label": label},
      el("table", {"data-testid": testid},
        el("thead", {}, el("tr", {},
          ["批次", "产线", "退役炉", "放行状态", "余量判定", "操作"].map(h => el("th", {scope: "col"}, h)))),
        el("tbody", {}, rows)));
  }

  function ovenChip(line, oven) {
    const chip = el("li", {
      class: `oven-chip oven-${oven.state}`,
      "data-testid": "oven-chip",
      "data-oven-code": oven.code,
      "data-state": oven.state,
    },
      el("span", {class: "oven-code"}, oven.code),
      el("span", {class: `oven-state state-${oven.state}`}, oven.state_label),
    );
    const actions = el("span", {class: "oven-actions"});
    if (canAct && oven.kind === "replacement" && oven.state === "pending_acceptance") {
      actions.append(actionButton("验收", "accept-replacement",
        {class: "primary tiny", data: {"data-oven-code": oven.code}},
        btn => call(btn, () => api.acceptOven(oven.code), r =>
          r.idempotent ? "该替换炉已验收，请勿重复计数。" : `替换炉 ${oven.code} 验收通过，已计入可用数。`)));
    } else if (canAct && oven.kind === "replacement" && oven.state === "available") {
      actions.append(actionButton("验收重试", "accept-replacement-retry",
        {class: "quiet tiny", data: {"data-oven-code": oven.code}},
        btn => call(btn, () => api.acceptOven(oven.code), r =>
          `验收请求已重试（第 ${r.attempts || 2} 次），仍只计 1 台，不重复计数。`)));
    } else if (canAct && oven.state === "delivery_failed") {
      actions.append(actionButton("确认恢复可用", "restore-oven",
        {class: "primary tiny", data: {"data-oven-code": oven.code}},
        btn => call(btn, () => api.restoreOven(oven.code), () =>
          `设备组已确认 ${oven.code} 恢复可用并计回。`)));
    }
    if (actions.childNodes.length) chip.append(actions);
    return chip;
  }

  function lineCards(board) {
    return el("div", {class: "line-grid", "data-testid": "line-grid"},
      board.lines.map(line => el("article", {
        class: "line-card panel-inner", "data-testid": "line-card", "data-line-code": line.code,
      },
        el("header", {},
          el("h3", {}, line.name || line.code, " ", el("span", {class: "code-tag"}, line.code)),
          line.note ? el("p", {class: "line-note"}, line.note) : null),
        el("p", {class: "usable-line", "data-testid": "usable-count", "data-count": String(line.usable_count)},
          "当前可用烘箱 ", el("b", {}, String(line.usable_count)), " 台（须保留至少 1 台）"),
        el("ul", {class: "count-chips"},
          el("li", {}, `待验收 ${line.counts.pending_acceptance || 0}`),
          el("li", {}, `已放行待交卷 ${line.counts.released || 0}`),
          el("li", {}, `交卷失败待恢复 ${line.counts.delivery_failed || 0}`),
          el("li", {}, `已交卷退役 ${line.counts.retired || 0}`)),
        el("ul", {class: "oven-list", "data-testid": "oven-list"}, line.ovens.map(o => ovenChip(line, o))),
      )));
  }

  function forms(board) {
    if (!canAct) return null;
    const lineOptions = board.lines.map(l => el("option", {value: l.code}, `${l.name}（${l.code}）`));

    const batchForm = el("form", {"data-testid": "create-batch-form", class: "inline-form"},
      el("div", {class: "form-row"},
        field("新退役批次号（可空）", el("input", {name: "code", placeholder: "RTB-XXXX", maxlength: "48"})),
        field("产线", el("select", {name: "line_code", required: true}, lineOptions)),
        field("退役炉号（逗号分隔，不同炉）", el("input", {
          name: "oven_codes", required: true, placeholder: "OV-501, OV-502",
        })),
        field("说明", el("input", {name: "title", maxlength: "120"}))),
      errorText(),
      el("button", {type: "submit", class: "primary"}, "登记退役批次"));
    submit(batchForm, async values => {
      const codes = String(values.get("oven_codes") || "").split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
      await api.createReleaseBatch({
        code: values.get("code") || undefined,
        line_code: values.get("line_code"),
        title: values.get("title") || "",
        oven_codes: codes,
      });
      batchForm.reset();
      notify("退役批次已登记，正在判定…", "ok");
      await paint();
    });

    const ovenForm = el("form", {"data-testid": "register-oven-form", class: "inline-form"},
      el("div", {class: "form-row"},
        field("烘箱/炉编号", el("input", {name: "code", required: true, placeholder: "OV-503", maxlength: "48"})),
        field("所属产线", el("select", {name: "line_code", required: true},
          board.lines.map(l => el("option", {value: l.code}, l.code)))),
        field("类型", el("select", {name: "kind"},
          el("option", {value: "replacement"}, "替换炉（到场待验收）"),
          el("option", {value: "old"}, "旧炉/在役炉")))),
      errorText(),
      el("button", {type: "submit"}, "登记烘箱"));
    submit(ovenForm, async values => {
      await api.createOven({
        code: values.get("code"), line_code: values.get("line_code"), kind: values.get("kind"),
      });
      ovenForm.reset();
      notify("烘箱已登记；替换炉需点「验收」后才计入可用数。", "ok");
      await paint();
    });

    const lineForm = el("form", {"data-testid": "create-line-form", class: "inline-form"},
      el("div", {class: "form-row"},
        field("产线编号", el("input", {name: "code", required: true, placeholder: "LINE-G", maxlength: "48"})),
        field("产线名称", el("input", {name: "name", placeholder: "焙烧七线", maxlength: "120"}))),
      errorText(),
      el("button", {type: "submit"}, "新增产线"));
    submit(lineForm, async values => {
      await api.createLine({code: values.get("code"), name: values.get("name") || values.get("code")});
      lineForm.reset();
      notify("产线已新增。", "ok");
      await paint();
    });

    return el("details", {class: "board-forms"},
      el("summary", {}, "登记新批次 / 替换炉 / 产线"),
      el("div", {class: "stack"}, batchForm, ovenForm, lineForm));
  }

  async function paint() {
    const board = await api.releaseBoard();
    const todo = board.batches.filter(b => b.todo);
    const done = board.batches.filter(b => !b.todo);
    body.replaceChildren(
      el("div", {class: "board-summary", "data-testid": "board-summary"},
        el("span", {class: "chip chip-todo"}, `待办批次 ${board.summary.todo_count}`),
        el("span", {class: "chip chip-shortage", "data-testid": "shortage-batch-count"},
          `待补足 ${board.summary.shortage_batch_count}`),
        el("span", {class: "chip chip-released"}, `已放行 ${board.summary.released_count}`),
        el("span", {class: "chip"}, `产线 ${board.summary.line_count}`)),
      el("h3", {class: "board-subhead"}, "待办批次：标明放行状态，缺口显示还缺几台，补足后点重新判定"),
      todo.length ? batchTable(todo, "todo-batch-table", "待办批次")
        : el("p", {class: "empty", "data-testid": "no-todo"}, "没有待办批次。"),
      done.length ? el("details", {class: "done-details"},
        el("summary", {}, `已办结批次（已交卷 / 已恢复，共 ${done.length} 批）`),
        batchTable(done, "done-batch-table", "已办结批次")) : null,
      el("h3", {class: "board-subhead"}, "产线烘箱余量与替换炉验收"),
      lineCards(board),
      forms(board),
    );
    return todo;
  }

  root.paint = paint;
  paint().catch(error => {
    body.replaceChildren(el("p", {role: "alert"}, `放行台账载入失败：${error.message}`));
  });
  return root;
}
