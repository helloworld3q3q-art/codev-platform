// 枚举元数据展示页 —— 只读，数据来自 useModel('enum')（POST /api/v1/enums/list）。
import { useModel } from '@umijs/max';
import { useMemo } from 'react';

import PageContainer from '@/components/PageContainer';

import EnumTable from './components/EnumTable';

export default function EnumsPage() {
  const { enumsGroup } = useModel('enum');

  const types = useMemo(() => Object.keys(enumsGroup ?? {}), [enumsGroup]);

  return (
    <PageContainer>
      {types.map((t) => (
        <EnumTable key={t} enumType={t} items={enumsGroup[t]} />
      ))}
    </PageContainer>
  );
}
