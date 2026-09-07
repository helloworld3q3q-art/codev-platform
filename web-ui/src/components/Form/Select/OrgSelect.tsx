// 顶栏组织切换器 —— 真切换: 调 /auth/switch-org 重签 session(新 token 绑新 org), RBAC 随之按新 org 判。
// 选项 = 本人所属 org(平台超管见全部); 切换成功后换 token + 重拉 initialState(roles/菜单按新 org)+ 重置项目。
import { useCallback } from 'react';
import { useModel } from '@umijs/max';

import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';

import { postOrgsList } from '@/services/apis/orgapi';
import { postSwitchOrg } from '@/services/apis/authapi';

type OrgSelectProps = Omit<SelectProps, 'fetchOptions'>;

const OrgSelect: React.FC<OrgSelectProps> = ({ ...restProps }) => {
  const { currentOrgId, setCurrentOrg } = useModel('org');
  const { setCurrentProject } = useModel('project');
  const { initialState } = useModel('@@initialState');

  const roles = initialState?.userInfo?.roles ?? [];
  const isPlatformAdmin = roles.includes('platform_admin');
  const myOrgs = initialState?.userInfo?.orgs ?? [];

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postOrgsList({ pageNumber: 1, pageSize: 200 });
        const list = result?.data ?? [];
        // 非平台超管只列本人所属 org(切到非成员 org 后端 403); 超管见全部(可跨 org 运维)。
        const scoped = isPlatformAdmin
          ? list
          : list.filter((item) => myOrgs.includes(item.code ?? ''));
        const options = scoped.map((item) => ({
          value: item.code ?? '',
          label: item.name ?? item.code ?? '',
          title: item.name ?? '',
          code: item.code ?? '',
          name: item.name ?? '',
          data: item,
        }));
        const filteredOptions = params.keyWord
          ? options.filter(
            (opt) =>
              opt.code.toLowerCase().includes(params.keyWord.toLowerCase()) ||
                opt.name.toLowerCase().includes(params.keyWord.toLowerCase()),
          )
          : options;
        return { data: filteredOptions, totalCounts: filteredOptions.length };
      } catch {
        return { data: [], totalCounts: 0 };
      }
    },
    [isPlatformAdmin, myOrgs],
  );

  const handleChange = useCallback(
    async (value: string): Promise<void> => {
      if (!value || value === currentOrgId) {
        return;
      }
      try {
        // 真切换: 后端校验成员身份后重签 session, 返回绑新 org 的 token 对。
        const res = await postSwitchOrg({ orgId: value });
        const pair = res.data;
        if (pair?.accessToken) {
          localStorage.setItem('auth_token', pair.accessToken);
          if (pair.refreshToken) {
            localStorage.setItem('refresh_token', pair.refreshToken);
          }
        }
        setCurrentOrg(value); // 写 current_org(fetch 注入 X-Org-Id)
        setCurrentProject(''); // 切 org 清项目, 重载后按新 org 重新默认
        // 整页重载: 新 token/org 已落 localStorage, reload 后 getInitialState 重取 roles/菜单,
        // 各页数据(用户表/项目等)也按新 org 重新拉 —— 仅 refresh() 不会刷新已渲染页面数据。
        window.location.reload();
      } catch {
        // 切换失败(非成员等)由 fetch 统一弹错; 不改本地状态。
      }
    },
    [currentOrgId, setCurrentOrg, setCurrentProject],
  );

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '选择组织'}
      style={restProps.style ?? { width: '100%' }}
      fetchOptions={handleFetchOptions}
      value={currentOrgId || undefined}
      onChange={handleChange}
      allowClear={false}
      showSearchPanel={false}
      showPagination={false}
      columnsWidth={[80, 120]}
      {...restProps}
    />
  );
};

export default OrgSelect;
