import Drawer from '@/components/Drawer';
import { showConfirm } from '@/components/Modal';
import Table from '@/components/Table';
import type { ProColumns } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Button, Form, Input, message, Select, Space, Spin } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  addMember,
  fetchMemberList,
  removeMember,
  setMemberRole,
  type MemberRow,
  type OrgRow,
} from '../utils';

export interface OrgMembersDrawerContext {
  open: boolean;
  record?: OrgRow;
}

interface OrgMembersDrawerProps {
  context: OrgMembersDrawerContext;
  onCancel: () => void;
}

interface RoleSelectProps {
  username: string;
  value?: string;
  options: { label: string; value: string }[];
  onChange: (username: string, role: string) => void;
}

// jsx-no-bind: 行内角色下拉提取子组件, 闭包 username 走 useCallback。
const RoleSelect: React.FC<RoleSelectProps> = ({ username, value, options, onChange }) => {
  const handleChange = useCallback(
    (role: string) => {
      onChange(username, role);
    },
    [username, onChange],
  );
  return <Select value={value} options={options} onChange={handleChange} className="w-120" />;
};

interface RemoveActionProps {
  record: MemberRow;
  onRemove: (record: MemberRow) => void;
}

// jsx-no-bind: 移除操作提取子组件。
const RemoveAction: React.FC<RemoveActionProps> = ({ record, onRemove }) => {
  const handleClick = useCallback(() => {
    onRemove(record);
  }, [record, onRemove]);
  return (
    <a className="text-#ff4d4f" onClick={handleClick}>
      移除
    </a>
  );
};

const OrgMembersDrawer: React.FC<OrgMembersDrawerProps> = ({ context, onCancel }) => {
  // 角色枚举走后端真值源 (useModel('enum')), 不前端硬编码。
  const { getEnumOptions } = useModel('enum');
  const [form] = Form.useForm();
  const { open, record } = context;
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const code = record?.code ?? '';
  const roleOptions = useMemo(() => getEnumOptions('MemberRoleEnum'), [getEnumOptions]);

  const loadMembers = useCallback(async (): Promise<void> => {
    if (!code) return;
    setLoading(true);
    try {
      const res = await fetchMemberList({ code, pageNumber: 1, pageSize: 200 });
      setMembers(res.data ?? []);
    } catch {
      setMembers([]);
    } finally {
      setLoading(false);
    }
  }, [code]);

  const handleAdd = useCallback(async (): Promise<void> => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await addMember({ code, username: values.username, role: values.role });
      message.success('成员已添加');
      form.resetFields();
      loadMembers();
    } finally {
      setSubmitting(false);
    }
  }, [form, code, loadMembers]);

  const handleRoleChange = useCallback(
    async (username: string, role: string): Promise<void> => {
      try {
        await setMemberRole({ code, username, role });
        message.success('角色已更新');
        loadMembers();
      } catch {
        // ignore
      }
    },
    [code, loadMembers],
  );

  const handleRemove = useCallback(
    (member: MemberRow): void => {
      showConfirm({
        title: `确认移除成员 "${member.username}"？`,
        onOk: async () => {
          await removeMember(code, member.username ?? '');
          message.success('成员已移除');
          loadMembers();
        },
      });
    },
    [code, loadMembers],
  );

  const columns = useMemo<ProColumns<MemberRow>[]>(
    () => [
      { title: '用户名', dataIndex: 'username', width: 180 },
      {
        title: '角色',
        dataIndex: 'role',
        width: 160,
        render: (_, member) => (
          <RoleSelect
            username={member.username ?? ''}
            value={member.role}
            options={roleOptions}
            onChange={handleRoleChange}
          />
        ),
      },
      {
        title: '操作',
        valueType: 'option',
        width: 100,
        render: (_, member) => <RemoveAction record={member} onRemove={handleRemove} />,
      },
    ],
    [roleOptions, handleRoleChange, handleRemove],
  );

  useEffect(() => {
    if (open && code) {
      form.resetFields();
      loadMembers();
    } else {
      setMembers([]);
    }
  }, [open, code, form, loadMembers]);

  return (
    <Drawer
      title={`成员管理 — ${record?.name ?? record?.code ?? ''}`}
      open={open}
      onCancel={onCancel}
      size="large"
      showOkButton={false}
      destroyOnHidden
    >
      <Form form={form} layout="inline" className="mb-16">
        <Form.Item
          name="username"
          rules={[{ required: true, message: '请输入用户名' }]}
        >
          <Input placeholder="成员用户名" className="w-180" />
        </Form.Item>
        <Form.Item name="role" initialValue="member">
          <Select options={roleOptions} placeholder="角色" className="w-120" />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" loading={submitting} onClick={handleAdd}>
              添加成员
            </Button>
          </Space>
        </Form.Item>
      </Form>
      <Spin spinning={loading}>
        <Table<MemberRow>
          rowKey="username"
          columns={columns}
          dataSource={members}
          pagination={false}
          search={false}
          options={false}
          toolBarRender={false}
        />
      </Spin>
    </Drawer>
  );
};

export default OrgMembersDrawer;
