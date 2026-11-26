import { retirementBoard } from "../components/retirement-board.js";
import { pageHeading } from "../components/common/panel.js";
import { el } from "../utils/dom.js";

// 「炉体退役批次」放行台账专页（同样的看板也内嵌在 #cases 页顶部）。
export async function batchesPage(config, user) {
  return el("div", {class: "page batches-page", "data-route-page": "batches"},
    pageHeading("炉体退役批次", "放行状态、还缺几台、补足后重新判定；并发申请按产线串行判定。"),
    retirementBoard(user));
}
