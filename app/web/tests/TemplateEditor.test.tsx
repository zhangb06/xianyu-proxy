import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import TemplateEditor from "../src/components/TemplateEditor";
import {
  FIXED_TEMPLATE_VARIABLES,
  sampleValues,
  TEMPLATE_VARIABLES,
  type TemplateVariable,
} from "../src/lib/template";

interface HarnessProps {
  initial: string;
  variables?: TemplateVariable[];
  values?: Record<string, string>;
}

function Harness({ initial, variables = TEMPLATE_VARIABLES, values = sampleValues() }: HarnessProps) {
  const [value, setValue] = useState(initial);
  return <TemplateEditor value={value} onChange={setValue} variables={variables} values={values} />;
}

describe("模板编辑器 - 选变量与实时预览", () => {
  it("点击变量按钮插入对应占位符，预览立即替换为示例值", async () => {
    render(<Harness initial="已付款：" />);

    await userEvent.click(screen.getByRole("button", { name: /卡\s*密/ }));

    expect(screen.getByRole("textbox")).toHaveValue("已付款：{secret}");
    expect(screen.getByText("已付款：CARD-8F2K-9Q1Z")).toBeInTheDocument();
  });

  it("在光标位置插入变量，而不是总追加到末尾", async () => {
    render(<Harness initial="订单：" />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    await userEvent.click(textarea);
    textarea.setSelectionRange(3, 3);

    await userEvent.click(screen.getByRole("button", { name: "订单号" }));

    expect(textarea).toHaveValue("订单：{order_no}");
  });

  it("修改文本时预览同步更新", () => {
    render(<Harness initial="订单号：{order_no}" />);

    expect(screen.getByText("订单号：2026110600001234")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "买家：{buyer}" },
    });

    expect(screen.getByText("买家：买家昵称")).toBeInTheDocument();
  });

  it("固定内容模式不提供卡密变量，预览中的卡密为空", () => {
    render(
      <Harness
        initial="卡密：{secret}"
        variables={FIXED_TEMPLATE_VARIABLES}
        values={sampleValues(undefined, { secret: "" })}
      />
    );

    expect(screen.queryByRole("button", { name: /卡\s*密/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "商品标题" })).toBeInTheDocument();
    expect(screen.getByText("卡密：")).toBeInTheDocument();
    expect(screen.queryByText(/CARD-8F2K-9Q1Z/)).not.toBeInTheDocument();
  });

  it("只提供必要变量，不展示订单金额和会话 ID", () => {
    render(<Harness initial="" />);

    expect(screen.getAllByRole("button")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /卡\s*密/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "订单号" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "商品标题" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "买家昵称" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "订单金额" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "会话 ID" })).not.toBeInTheDocument();
  });
});
