import { useRef } from "react";
import { Button, Card, Input, Space, Tooltip, Typography } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { renderTemplate, type TemplateVariable } from "../lib/template";

interface TemplateEditorProps {
  value?: string;
  onChange?: (value: string) => void;
  id?: string;
  rows?: number;
  variables: TemplateVariable[];
  values: Record<string, string>;
  placeholder?: string;
}

interface Selection {
  start: number;
  end: number;
}

export default function TemplateEditor({
  value,
  onChange,
  id,
  rows = 5,
  variables,
  values,
  placeholder,
}: TemplateEditorProps) {
  const textareaRef = useRef<TextAreaRef>(null);
  const selectionRef = useRef<Selection | null>(null);
  const current = value ?? "";

  const rememberSelection = (element: HTMLTextAreaElement) => {
    selectionRef.current = {
      start: element.selectionStart ?? 0,
      end: element.selectionEnd ?? 0,
    };
  };

  const insert = (variable: TemplateVariable) => {
    const textarea = textareaRef.current?.resizableTextArea?.textArea;
    const activeSelection =
      textarea && document.activeElement === textarea
        ? { start: textarea.selectionStart ?? 0, end: textarea.selectionEnd ?? 0 }
        : selectionRef.current ?? { start: current.length, end: current.length };
    const next =
      current.slice(0, activeSelection.start) +
      variable.token +
      current.slice(activeSelection.end);
    onChange?.(next);
    const caret = activeSelection.start + variable.token.length;
    selectionRef.current = { start: caret, end: caret };
    window.setTimeout(() => {
      const node = textareaRef.current?.resizableTextArea?.textArea;
      node?.focus();
      node?.setSelectionRange(caret, caret);
    }, 0);
  };

  const preview = renderTemplate(current, values);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="small">
      <Space size={[8, 8]} wrap>
        <Typography.Text type="secondary">点击插入变量：</Typography.Text>
        {variables.map((variable) => (
          <Tooltip key={variable.key} title={`${variable.token}，示例：${variable.sample}`}>
            <Button size="small" type="dashed" onClick={() => insert(variable)}>
              {variable.label}
            </Button>
          </Tooltip>
        ))}
      </Space>
      <Input.TextArea
        ref={textareaRef}
        id={id}
        rows={rows}
        value={current}
        placeholder={placeholder}
        onChange={(event) => onChange?.(event.target.value)}
        onSelect={(event) => rememberSelection(event.currentTarget)}
        onKeyUp={(event) => rememberSelection(event.currentTarget)}
        onClick={(event) => rememberSelection(event.currentTarget)}
        onBlur={(event) => rememberSelection(event.currentTarget)}
      />
      <Card size="small" title="实时预览" style={{ background: "#fafafa" }}>
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", margin: 0 }}>
          {preview || (
            <Typography.Text type="secondary">输入内容或插入变量后，这里会显示发送效果</Typography.Text>
          )}
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          预览使用示例数据，实际发送时会替换为真实订单信息。
        </Typography.Text>
      </Card>
    </Space>
  );
}
