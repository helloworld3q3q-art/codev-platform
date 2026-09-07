import PageContainer from '@/components/PageContainer';
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import type { ActionType } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { message } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { createColumns } from './components/Columns';
import ProjectFormDrawer, { type ProjectFormDrawerContext } from './components/ProjectFormDrawer';
import ToolBarRender from './components/ToolBarRender';
import {
  convertParams,
  fetchProjectList,
  loadProject,
  unloadProject,
  type ProjectRow,
} from './components/utils';

const FORM_DEFAULT: ProjectFormDrawerContext = { open: false };

const ProjectsPage: React.FC = () => {
  // 状态枚举走后端真值源 (useModel('enum')), 不前端硬编码。
  const { getFormattedEnums } = useModel('enum');
  // 订阅当前组织, 切组织后项目列表(按 org 过滤)原地重拉(fetch 注入 X-Org-Id)。
  const { currentOrgId } = useModel('org');
  const actionRef = useRef<ActionType>();
  const [formCtx, setFormCtx] = useState<ProjectFormDrawerContext>(FORM_DEFAULT);

  const statusMap = useMemo(() => getFormattedEnums('ProjectStatusEnum'), [getFormattedEnums]);

  // 切组织后让 ResizableTable 重新走 request(按新 X-Org-Id 过滤)。
  useEffect(() => {
    actionRef.current?.reload();
  }, [currentOrgId]);

  const handleAdd = useCallback(() => {
    setFormCtx({ open: true });
  }, []);

  const handleCancel = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
  }, []);

  const handleOk = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
    actionRef.current?.reload();
  }, []);

  const handleLoad = useCallback(async (record: ProjectRow) => {
    try {
      await loadProject(record.code);
      message.success('已加载');
      actionRef.current?.reload();
    } catch {
      // ignore
    }
  }, []);

  const handleUnload = useCallback(async (record: ProjectRow) => {
    try {
      await unloadProject(record.code);
      message.success('已卸载');
      actionRef.current?.reload();
    } catch {
      // ignore
    }
  }, []);

  const handleTableRequest = useCallback(
    async (params: Record<string, any>, sort: any, filter: any) =>
      requestWrapper(params, sort, filter, fetchProjectList, convertParams),
    [],
  );

  const handleToolBarRender = useCallback(() => ToolBarRender({ onAdd: handleAdd }), [handleAdd]);

  const columns = useMemo(
    () =>
      createColumns({
        context: { statusMap },
        onLoad: handleLoad,
        onUnload: handleUnload,
      }),
    [statusMap, handleLoad, handleUnload],
  );

  return (
    <PageContainer>
      <ResizableTable<ProjectRow>
        actionRef={actionRef}
        rowKey="code"
        columns={columns}
        request={handleTableRequest}
        toolBarRender={handleToolBarRender}
        scroll={{ x: 1000 }}
        pagination={{ defaultPageSize: 10 }}
        search={{ span: 6, layout: 'vertical', defaultCollapsed: false }}
        form={{ layout: 'vertical', colon: false }}
      />

      <ProjectFormDrawer context={formCtx} onOk={handleOk} onCancel={handleCancel} />
    </PageContainer>
  );
};

export default ProjectsPage;
