import { PermissionButton } from '@/components/Button';
import { showConfirm } from '@/components/Modal';
import PageContainer from '@/components/PageContainer';
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import { PlusOutlined } from '@ant-design/icons';
import type { ActionType } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { message } from 'antd';
import { useCallback, useMemo, useRef, useState } from 'react';

import { createColumns } from './components/Columns';
import OrgFormDrawer, { type OrgFormDrawerContext } from './components/OrgFormDrawer';
import OrgMembersDrawer, { type OrgMembersDrawerContext } from './components/OrgMembersDrawer';
import {
  convertParams,
  fetchOrgList,
  ORG_STATUS_ACTIVE,
  ORG_STATUS_DISABLED,
  setOrgStatus,
  type OrgRow,
} from './components/utils';

const FORM_DEFAULT: OrgFormDrawerContext = { open: false, mode: 'create' };
const MEMBERS_DEFAULT: OrgMembersDrawerContext = { open: false };

const OrgsPage: React.FC = () => {
  // 状态枚举走后端真值源 (useModel('enum')), 不前端硬编码。
  const { getFormattedEnums } = useModel('enum');
  const actionRef = useRef<ActionType>();
  const [formCtx, setFormCtx] = useState<OrgFormDrawerContext>(FORM_DEFAULT);
  const [membersCtx, setMembersCtx] = useState<OrgMembersDrawerContext>(MEMBERS_DEFAULT);

  const statusMap = useMemo(() => getFormattedEnums('OrgStatusEnum'), [getFormattedEnums]);

  const handleAdd = useCallback(() => {
    setFormCtx({ open: true, mode: 'create' });
  }, []);

  const handleEdit = useCallback((record: OrgRow) => {
    setFormCtx({ open: true, mode: 'edit', record });
  }, []);

  const handleFormOk = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
    actionRef.current?.reload();
  }, []);

  const handleFormCancel = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
  }, []);

  const handleMembers = useCallback((record: OrgRow) => {
    setMembersCtx({ open: true, record });
  }, []);

  const handleMembersCancel = useCallback(() => {
    setMembersCtx(MEMBERS_DEFAULT);
  }, []);

  const handleToggleStatus = useCallback((record: OrgRow) => {
    const isActive = record.status === ORG_STATUS_ACTIVE;
    const next = isActive ? ORG_STATUS_DISABLED : ORG_STATUS_ACTIVE;
    showConfirm({
      title: `确认${isActive ? '禁用' : '启用'}组织 "${record.code}"？`,
      content: isActive ? '禁用后该组织不可用' : undefined,
      onOk: async () => {
        await setOrgStatus(record.code, next);
        message.success(isActive ? '已禁用' : '已启用');
        actionRef.current?.reload();
      },
    });
  }, []);

  const columns = useMemo(
    () =>
      createColumns({
        context: { statusMap },
        onEdit: handleEdit,
        onToggleStatus: handleToggleStatus,
        onMembers: handleMembers,
      }),
    [statusMap, handleEdit, handleToggleStatus, handleMembers],
  );

  const handleTableRequest = useCallback(
    async (params: Record<string, any>, sort: any, filter: any) =>
      requestWrapper(params, sort, filter, fetchOrgList, convertParams),
    [],
  );

  const handleToolBarRender = useCallback(
    () => [
      <PermissionButton key="add" type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
        新增组织
      </PermissionButton>,
    ],
    [handleAdd],
  );

  return (
    <PageContainer>
      <ResizableTable<OrgRow>
        actionRef={actionRef}
        rowKey="code"
        columns={columns}
        request={handleTableRequest}
        toolBarRender={handleToolBarRender}
        scroll={{ x: 1000 }}
        pagination={{ defaultPageSize: 10 }}
        search={false}
      />

      <OrgFormDrawer context={formCtx} onOk={handleFormOk} onCancel={handleFormCancel} />
      <OrgMembersDrawer context={membersCtx} onCancel={handleMembersCancel} />
    </PageContainer>
  );
};

export default OrgsPage;
