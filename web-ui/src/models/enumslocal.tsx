/**
 * 枚举数据文件 - 自动生成，实际业务从接口获取，生成枚举文件只为展示，不做实际业务取值，或者编程取值
 * 注意：此文件仅用于展示枚举数据，不应该直接在业务代码中使用。
 * 提供给AI识别业务，用于生成业务代码。
 * 自动生成于: 2026-06-02T07:08:55.876Z
 * 请勿手动修改此文件
 */


// 枚举项类型定义
export interface EnumItem {
  value: string;
  label: string;
  order: number;
  description?: string;
}

// 枚举组类型定义
export interface EnumGroup {
  [enumType: string]: EnumItem[];
}


// 枚举数据
const ENUMS_LOCAL: EnumGroup = {
  OrgStatusEnum: [
    {
      value: "ACTIVE",
      label: "启用",
      order: 1,
      description: "组织正常使用"
    },
    {
      value: "DISABLED",
      label: "禁用",
      order: 2,
      description: "组织禁用, 其下项目不可访问"
    }
  ],
  UserStatusEnum: [
    {
      value: "ACTIVE",
      label: "启用",
      order: 1,
      description: "用户正常"
    },
    {
      value: "DISABLED",
      label: "禁用",
      order: 2,
      description: "用户禁用, token/session 失效"
    }
  ],
  ProjectStatusEnum: [
    {
      value: "ACTIVE",
      label: "启用",
      order: 1,
      description: "项目正常使用"
    },
    {
      value: "ARCHIVED",
      label: "归档",
      order: 2,
      description: "项目已归档, 只读"
    }
  ],
  MemberRoleEnum: [
    {
      value: "viewer",
      label: "只读",
      order: 1,
      description: "只读访问"
    },
    {
      value: "member",
      label: "成员",
      order: 2,
      description: "可写, 触发分析"
    },
    {
      value: "admin",
      label: "管理员",
      order: 3,
      description: "管理成员/配置"
    }
  ],
  JobStatusEnum: [
    {
      value: "Pending",
      label: "排队中",
      order: 1,
      description: "JobStatusEnum - 排队中"
    },
    {
      value: "Running",
      label: "运行中",
      order: 2,
      description: "JobStatusEnum - 运行中"
    },
    {
      value: "Succeeded",
      label: "成功",
      order: 3,
      description: "JobStatusEnum - 成功"
    },
    {
      value: "Failed",
      label: "失败",
      order: 4,
      description: "JobStatusEnum - 失败"
    },
    {
      value: "Cancelled",
      label: "已取消",
      order: 5,
      description: "JobStatusEnum - 已取消"
    }
  ]
};
export default ENUMS_LOCAL;
