import { showConfirm } from '@/components/Modal';
import PageContainer from '@/components/PageContainer';
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import { PlusOutlined } from '@ant-design/icons';
import type { ActionType } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Button, message } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import AssignRolesDrawer, { type AssignRolesDrawerContext } from './components/AssignRolesDrawer';
import { createColumns } from './components/Columns';
import UserFormDrawer, { type UserFormDrawerContext } from './components/UserFormDrawer';
import {
  convertParams,
  fetchOrgOptions,
  fetchUserList,
  resetUserPassword,
  setUserStatus,
  type UserRow,
} from './components/utils';

const FORM_DEFAULT: UserFormDrawerContext = { open: false, mode: 'create' };
const ASSIGN_DEFAULT: AssignRolesDrawerContext = { open: false };
const DEFAULT_PASSWORD = 'Admin@123';

const UsersPage: React.FC = () => {
  // 状态 / 角色枚举走后端真值源 (useModel('enum')), 不前端硬编码。
  const { getEnumOptions, getFormattedEnums } = useModel('enum');
  const actionRef = useRef<ActionType>();
  const [formCtx, setFormCtx] = useState<UserFormDrawerContext>(FORM_DEFAULT);
  const [assignCtx, setAssignCtx] = useState<AssignRolesDrawerContext>(ASSIGN_DEFAULT);
  const [orgOptions, setOrgOptions] = useState<{ label: string; value: string }[]>([]);

  const statusMap = useMemo(() => getFormattedEnums('UserStatusEnum'), [getFormattedEnums]);
  const roleMap = useMemo(() => getFormattedEnums('MemberRoleEnum'), [getFormattedEnums]);
  const roleOptions = useMemo(() => getEnumOptions('MemberRoleEnum'), [getEnumOptions]);

  const loadOrgOptions = useCallback(async (): Promise<void> => {
    try {
      const options = await fetchOrgOptions();
      setOrgOptions(options);
    } catch {
      setOrgOptions([]);
    }
  }, []);

  const handleAdd = useCallback(() => {
    setFormCtx({ open: true, mode: 'create' });
  }, []);

  const handleEdit = useCallback((record: UserRow) => {
    setFormCtx({ open: true, mode: 'edit', record });
  }, []);

  const handleFormOk = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
    actionRef.current?.reload();
  }, []);

  const handleFormCancel = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
  }, []);

  const handleChangeRole = useCallback((record: UserRow) => {
    setAssignCtx({ open: true, record });
  }, []);

  const handleAssignOk = useCallback(() => {
    setAssignCtx(ASSIGN_DEFAULT);
    actionRef.current?.reload();
  }, []);

  const handleAssignCancel = useCallback(() => {
    setAssignCtx(ASSIGN_DEFAULT);
  }, []);

  const handleToggleStatus = useCallback((record: UserRow) => {
    const active = record.status === 'ACTIVE';
    const nextStatus = active ? 'DISABLED' : 'ACTIVE';
    showConfirm({
      title: `确认${active ? '禁用' : '启用'}用户 "${record.username}"？`,
      content: active ? '禁用后该用户无法登录, 现有会话将被撤销' : undefined,
      onOk: async () => {
        await setUserStatus(record.username, nextStatus);
        message.success(active ? '已禁用' : '已启用');
        actionRef.current?.reload();
      },
    });
  }, []);

  const handleResetPwd = useCallback((record: UserRow) => {
    showConfirm({
      title: `重置 "${record.username}" 的密码？`,
      content: `重置为 ${DEFAULT_PASSWORD}, 登录后请立即修改`,
      onOk: async () => {
        await resetUserPassword(record.username, DEFAULT_PASSWORD);
        message.success(`密码已重置为 ${DEFAULT_PASSWORD}`);
      },
    });
  }, []);

  const columns = useMemo(
    () =>
      createColumns({
        context: {
          statusMap,
          roleMap,
          onEdit: handleEdit,
          onToggleStatus: handleToggleStatus,
          onResetPwd: handleResetPwd,
          onChangeRole: handleChangeRole,
        },
      }),
    [statusMap, roleMap, handleEdit, handleToggleStatus, handleResetPwd, handleChangeRole],
  );

  const handleTableRequest = useCallback(
    async (params: Record<string, any>, sort: any, filter: any) =>
      requestWrapper(params, sort, filter, fetchUserList, convertParams),
    [],
  );

  const handleToolBarRender = useCallback(
    () => [
      <Button key="add" type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
        新增用户
      </Button>,
    ],
    [handleAdd],
  );

  useEffect(() => {
    loadOrgOptions();
  }, [loadOrgOptions]);

  return (
    <PageContainer>
      <ResizableTable<UserRow>
        actionRef={actionRef}
        rowKey="username"
        columns={columns}
        request={handleTableRequest}
        toolBarRender={handleToolBarRender}
        scroll={{ x: 1000 }}
        pagination={{ defaultPageSize: 20 }}
        search={false}
      />
      <UserFormDrawer
        context={formCtx}
        orgOptions={orgOptions}
        roleOptions={roleOptions}
        onOk={handleFormOk}
        onCancel={handleFormCancel}
      />
      <AssignRolesDrawer
        context={assignCtx}
        orgOptions={orgOptions}
        roleOptions={roleOptions}
        onOk={handleAssignOk}
        onCancel={handleAssignCancel}
      />
    </PageContainer>
  );
};

export default UsersPage;
