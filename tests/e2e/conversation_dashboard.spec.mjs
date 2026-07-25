import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test.describe.configure({ mode: "serial" });
const auditDir = ".test_workspaces/audit-current";

test.beforeAll(async () => {
  await mkdir(auditDir, { recursive: true });
});

async function expectNoHorizontalOverflow(page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
}

async function projectApi(request) {
  const projectsResponse = await request.get("/api/v2/projects");
  expect(projectsResponse.ok()).toBeTruthy();
  const projects = await projectsResponse.json();
  const project = projects.find((item) => item.conversation_count > 0) ?? projects[0];
  expect(project).toBeTruthy();
  return `/api/v2/projects/${encodeURIComponent(project.project_id)}`;
}

test("desktop creates a task and opens its logical Agent terminal", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "MuxDev 工作台" })).toBeVisible();
  const rollbackConversation = page.getByRole("button", {
    name: "Rollback verified browser delivery",
  });
  await expect(rollbackConversation).toBeVisible();
  await rollbackConversation.click();
  await expect(
    page.getByRole("heading", { name: "Rollback verified browser delivery" }),
  ).toBeVisible();
  await page.screenshot({
    path: `${auditDir}/01-session-rail-and-conversation.png`,
    fullPage: false,
  });
  const createButton = page.getByRole("button", { name: "新建任务" });
  const api = await projectApi(page.request);
  const agentResponse = await page.request.get(`${api}/agents`);
  expect(agentResponse.ok()).toBeTruthy();
  const agentDefinitions = await agentResponse.json();

  await createButton.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(createButton).toBeFocused();

  await createButton.click();
  const agentSelect = page.getByLabel("主要 Agent");
  await expect(agentSelect.locator('optgroup[label^="已检测到"]')).toHaveCount(1);
  const qwenDefinition = agentDefinitions.find(
    (definition) => definition.agent_id === "qwen",
  );
  expect(qwenDefinition).toBeTruthy();
  const qwenOption = agentSelect.locator('option[value="qwen"]');
  await expect(qwenOption).toContainText("Qwen Code");
  if (qwenDefinition.available) {
    await expect(qwenOption).toBeEnabled();
  } else {
    await expect(qwenOption).toBeDisabled();
  }
  for (const definition of agentDefinitions) {
    const option = agentSelect.locator(`option[value="${definition.agent_id}"]`);
    if (!definition.available) await expect(option).toHaveAttribute("disabled", "");
  }
  await expect
    .poll(() =>
      agentSelect.locator("option:checked").evaluate((option) => option.disabled),
    )
    .toBe(false);
  await page.getByLabel("任务目标").fill("Inspect README.md and report findings");
  await agentSelect.selectOption("mock");
  await page.getByLabel("最终想拿到什么").selectOption("answer");
  await page.screenshot({
    path: `${auditDir}/02-create-dialog-agent-availability.png`,
    fullPage: false,
  });
  await page.getByRole("button", { name: "创建任务" }).click();
  const createdConversation = page.getByRole("button", {
    name: "Inspect README.md and report findings",
  });
  await expect(createdConversation).toBeVisible();
  await createdConversation.click();
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "Terminal" }).click();
  const terminalButton = page.getByRole("button", { name: /打开终端/ }).first();
  await expect(terminalButton).toBeVisible();
  await terminalButton.click();
  await expect(page.getByLabel("多 Agent Web 终端")).toBeVisible();
  await expect(page.locator("#terminal-lease-status")).toContainText(
    /连接中|已连接|只读|可写/,
  );
  await page.screenshot({
    path: `${auditDir}/03-created-conversation-terminal.png`,
    fullPage: false,
  });
});

test("failed Session keeps transcript read-only and restarts explicitly", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Restart failed Agent Session" })
    .click();
  await page.getByRole("button", { name: "Terminal" }).click();

  const readOnly = page.getByRole("button", {
    name: "只读查看失败输出",
  });
  await expect(readOnly).toBeVisible();
  await readOnly.click();
  await expect(page.getByRole("alert")).toContainText(
    "Simulated Agent process exit",
  );
  await expect(page.locator("#terminal-lease-status")).toContainText(
    "失败输出（只读）",
  );

  await page.getByRole("button", { name: "重新启动" }).click();
  await expect(
    page.getByLabel("多 Agent Web 终端").locator("option:checked"),
  ).toContainText("G2");
  await expect(page.getByRole("alert")).toBeHidden();
  await expect(page.locator("#terminal-lease-status")).toContainText(
    /已连接|只读/,
  );
  const composer = page.getByLabel("发送消息");
  await composer.fill("Continue after the explicit restart");
  const sentResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes("/conversations/") &&
      response.url().endsWith("/messages"),
  );
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const response = await sentResponse;
  expect(response.status(), await response.text()).toBe(202);
  await expect(
    page.getByRole("status").filter({ hasText: "消息已发送" }),
  ).toBeVisible();
});

test("390x844 keeps the composer reachable and answers clarification by keyboard", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const goal = `帮我优化一下 ${Date.now()}`;
  const api = await projectApi(page.request);
  const created = await page.request.post(`${api}/conversations`, {
    data: { goal, mode: "direct", agent_id: "mock", auto_start_when_ready: true },
  });
  expect(created.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(goal) }).click();
  await expect(page.getByText("需要你确认", { exact: true }).first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const composer = page.locator("#composer");
  await composer.scrollIntoViewIfNeeded();
  await expect(composer).toBeVisible();
  const box = await composer.boundingBox();
  expect(box).not.toBeNull();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);

  const answer = page.getByLabel("自定义回答");
  await answer.fill("交付一份结构化分析报告");
  await answer.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "回答已提交" })).toBeVisible();
  await expect(page.getByText("需要你确认", { exact: true }).first()).toBeHidden();
  await expect(answer).toBeHidden();
  await expect(page.locator(".conversation-status")).toContainText("进行中");
  await page.screenshot({
    path: `${auditDir}/04-mobile-clarification.png`,
    fullPage: true,
  });
});

test("1024x768 keeps Conversation and Changes usable without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Harden checkout retry handling" })
    .click();
  await page.getByRole("button", { name: "Changes" }).click();
  await expect(page.getByRole("button", { name: /src\/checkout\.py/ })).toBeVisible();
  await expect(page.getByLabel("活动时间线")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: `${auditDir}/05-tablet-changes.png`,
    fullPage: false,
  });
});

test("1440x900 renders the reviewed Changes state without console errors", async ({
  page,
}) => {
  const browserErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      browserErrors.push(`${response.status()} ${response.url()}`);
    }
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Harden checkout retry handling" })
    .click();
  await page.getByRole("button", { name: "Changes" }).click();
  const changedFile = page.getByRole("button", { name: /src\/checkout\.py/ });
  await expect(changedFile).toBeVisible();
  await expect(changedFile).toContainText("+1");
  await expect(changedFile).toContainText("−1");
  await expectNoHorizontalOverflow(page);
  await page.screenshot({
    path: `${auditDir}/06-desktop-changes.png`,
    fullPage: false,
  });
  expect(browserErrors).toEqual([]);
});

test("review request-changes preserves the draft until success and starts the next turn", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Revise verified browser delivery" })
    .click();
  await page.getByRole("button", { name: "Changes" }).click();
  await expect(
    page.getByRole("button", { name: /revise-browser\.txt/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Review" }).click();
  await expect(page.getByText(/本轮变更 \d+ 个文件/)).toBeVisible();
  const feedback = page.getByLabel("修改意见");
  await feedback.fill("请补充空输入的边界测试。");
  await page.screenshot({
    path: `${auditDir}/07-review-request-changes.png`,
    fullPage: false,
  });
  await page
    .locator(".review-feedback")
    .getByRole("button", { name: "请求修改" })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "修改意见已发送" }),
  ).toBeVisible();
  await expect(page.locator(".conversation-status")).toContainText("进行中");
  await expect(feedback).toBeHidden();
});

test("accept settles a genuine Evidence v3 candidate to idle", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Accept verified browser delivery" })
    .click();
  await page.getByRole("button", { name: "Changes" }).click();
  await expect(
    page.getByRole("button", { name: /accept-browser\.txt/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Review" }).click();
  await page.screenshot({
    path: `${auditDir}/08-review-before-accept.png`,
    fullPage: false,
  });
  await page.getByRole("button", { name: "接受变更" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "本轮变更已接受" }),
  ).toBeVisible();
  await expect(page.locator(".conversation-status")).toContainText("待命");
});

test("rollback restores bytes, settles idle, and appears in the timeline", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Rollback verified browser delivery" })
    .click();
  await page.getByRole("button", { name: "Review" }).click();
  await page.getByRole("button", { name: "回退本轮" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "本轮变更已安全回退" }),
  ).toBeVisible();
  await expect(page.locator(".conversation-status")).toContainText("待命");
  await expect(page.getByText("本轮变更已安全回退").first()).toBeVisible();
  await page.screenshot({
    path: `${auditDir}/09-rollback-timeline.png`,
    fullPage: false,
  });
  await expectNoHorizontalOverflow(page);
});

test("Rule canvas creates a safe custom Rule without arbitrary shell input", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Harden checkout retry handling" })
    .click();
  await page.getByRole("button", { name: "Rule" }).click();
  await page.getByRole("button", { name: "自定义 Rule" }).click();

  const suffix = Date.now();
  const ruleId = `browser.safe-change-${suffix}`;
  const title = `Browser 安全变更 ${suffix}`;
  await page.getByLabel("Rule ID").fill(ruleId);
  await page.getByLabel("标题", { exact: true }).fill(title);
  await page
    .getByLabel("门禁内容与交付标准")
    .fill("只修改任务范围内文件，并提供聚焦回归证据。");
  await page.getByRole("button", { name: "保存到个人 Rule 库" }).click();

  await expect(
    page.getByRole("status").filter({ hasText: `Rule 已创建：${title}` }),
  ).toBeVisible();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await expect(page.getByText(/CI 门禁只能由已登记的 argv 验证命令/)).toBeHidden();
  await expectNoHorizontalOverflow(page);
});
