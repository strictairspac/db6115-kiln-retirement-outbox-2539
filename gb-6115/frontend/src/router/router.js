const routes = ["overview", "cases", "runs", "evidence"];
// 放行台账专页的额外 hash 别名；导航主体仍保持四个模块不变。
const aliasRoutes = {batches: "batches", release: "batches", retirement: "batches"};
const extraRoutes = Object.keys(aliasRoutes);

export function activeRoute() {
  const [name, id] = (location.hash.slice(1) || "overview").split("/");
  let resolved = routes.includes(name) ? name : (aliasRoutes[name] || "overview");
  return {name: resolved, rawName: name, id: /^\d+$/.test(id || "") ? Number(id) : null};
}

export function navigate(route) {
  location.hash = route;
}

export { routes, extraRoutes };
