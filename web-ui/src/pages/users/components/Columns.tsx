import { DownOutlined } from '@ant-design/icons';
import type { ProColumns } from '@ant-design/pro-components';
import type { MenuProps } from 'antd';
import { Badge, Dropdown, Tag, Typography } from 'antd';
import { useCallback, useMemo } from 'react';

import type { UserRow } from './utils';
import { ROLE_COLOR, STATUS_BADGE } from './utils';

interface ActionProps {
  record: UserRow;
  onClick: (record: UserRow) => void;
}

// jsx-no-bind: 操作列回调提取子组件 + useCallback。
const EditAction: React.FC<ActionProps> = ({ record, onClick }) => {
  const handleClick = useCallback(() => onClick(record), [record, onClick]);
  return <Typography.Link onClick={handleClick}>编辑</Typography.Link>;
};

interface StatusActionProps extends ActionProps {
  active: boolean;
}

const StatusAction: React.FC<StatusActionProps> = ({ record, active, onClick }) => {
  const handleClick = useCallback(() => onClick(record), [record, onClick]);
  return <Typography.Link onClick={handleClick}>{active ? '禁用' : '启用'}</Typography.Link>;
};

interface MoreActionProps {
  record: UserRow;
  onResetPwd: (record: UserRow) => void;
  onChangeRole: (record: UserRow) => void;
}

const MoreAction: React.FC<MoreActionProps> = ({ record, onResetPwd, onChangeRole }) => {
  const handleReset = useCallback(() => onResetPwd(record), [record, onResetPwd]);
  const handleRole = useCallback(() => onChangeRole(record), [record, onChangeRole]);
  const items: MenuProps['items'] = useMemo(
    () => [
      { key: 'reset', label: '重置密码', onClick: handleReset },
      { key: 'role', label: '改角色', onClick: handleRole },
    ],
    [handleReset, handleRole],
  );
  const menu = useMemo(() => ({ items }), [items]);
  return (
    <Dropdown menu={menu} trigger={['click']}>
      <Typography.Link>
        更多 <DownOutlined className="text-12" />
      </Typography.Link>
    </Dropdown>
  );
};

interface CreateColumnsContext {
  statusMap: Record<string, string>;
  roleMap: Record<string, string>;
  onEdit: (record: UserRow) => void;
  onToggleStatus: (record: UserRow) => void;
  onResetPwd: (record: UserRow) => void;
  onChangeRole: (record: UserRow) => void;
}

export function createColumns({
  context,
}: {
  context: CreateColumnsContext;
}): ProColumns<UserRow>[] {
  const { statusMap, roleMap, onEdit, onToggleStatus, onResetPwd, onChangeRole } = context;

  return [
    { title: '用户名', dataIndex: 'username', width: 160, copyable: true, search: false },
    { title: '显示名', dataIndex: 'displayName', width: 160, search: false },
    { title: '邮箱', dataIndex: 'email', width: 200, ellipsis: true, search: false },
    { title: '组织', dataIndex: 'orgId', width: 160, search: false },
    {
      title: '角色',
      dataIndex: 'role',
      width: 110,
      search: false,
      render: (_, record) => {
        const role = record.role ?? '';
        return role ? <Tag color={ROLE_COLOR[role] ?? 'default'}>{roleMap[role] ?? role}</Tag> : '-';
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      search: false,
      render: (_, record) => {
        const status = record.status ?? '';
        return (
          <Badge
            status={STATUS_BADGE[status] ?? 'default'}
            text={statusMap[status] ?? status ?? '-'}
          />
        );
      },
    },
    {
      title: '操作',
      valueType: 'option',
      width: 180,
      fixed: 'right',
      render: (_, record) => {
        const active = record.status === 'ACTIVE';
        return (
          <div className="grid grid-cols-2 gap-x-12 gap-y-4">
            <EditAction record={record} onClick={onEdit} />
            <StatusAction record={record} active={active} onClick={onToggleStatus} />
            <MoreAction record={record} onResetPwd={onResetPwd} onChangeRole={onChangeRole} />
          </div>
        );
      },
    },
  ];
}
