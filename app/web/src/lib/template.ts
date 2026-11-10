export interface TemplateVariable {
  key: string;
  label: string;
  token: string;
  sample: string;
}

export const TEMPLATE_VARIABLES: TemplateVariable[] = [
  { key: "secret", label: "卡密", token: "{secret}", sample: "CARD-8F2K-9Q1Z" },
  { key: "order_no", label: "订单号", token: "{order_no}", sample: "2026110600001234" },
  { key: "item_title", label: "商品标题", token: "{item_title}", sample: "商品标题" },
  { key: "buyer", label: "买家昵称", token: "{buyer}", sample: "买家昵称" },
];

export const FIXED_TEMPLATE_VARIABLES = TEMPLATE_VARIABLES.filter(
  (variable) => variable.key !== "secret"
);

export function sampleValues(
  itemTitle = "商品标题",
  overrides: Record<string, string> = {}
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const variable of TEMPLATE_VARIABLES) {
    values[variable.key] = variable.sample;
  }
  values.item_title = itemTitle || values.item_title;
  return { ...values, ...overrides };
}

export function renderTemplate(template: string, values: Record<string, string>): string {
  return (template || "").replace(/\{([a-z_]+)\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? values[key] : match
  );
}
