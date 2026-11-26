export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  create_line: "新增产线", create_oven: "登记烘箱", create_release_batch: "登记退役批次",
  release_batch: "放行", block_batch: "判定不放行", judge_batch: "重新判定",
  accept_oven: "替换炉验收", accept_retry: "验收重试",
  submit_batch: "退役交卷", submit_failed: "交卷失败",
  confirm_restore: "设备组确认恢复", batch_restored: "批次恢复收尾",
});
