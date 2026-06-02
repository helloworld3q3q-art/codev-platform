import { post } from '@/utils/fetch';
import { useCallback, useEffect, useState } from 'react';

// 定义 Select options 的类型
interface SelectOption {
  value: string;
  label: string;
}

export interface EnumItemDTO {
  enumType?: string;
  enumValue?: string;
  localLanguage?: string;
  enumOrder?: number;
  displayName?: string;
  // 业务说明，供 tooltip 等场景展示规则 / 指标的专业解读
  description?: string;
}

// 携带 description 字段的下拉选项类型，供 tooltip 场景使用
interface SelectOptionWithDescription {
  value: string;
  label: string;
  description: string;
}

export interface EnumItemGrouped {
  [enumType: string]: EnumItemDTO[];
}

// 模块级工具函数：按枚举 order 升序排序，避免在 Hook 函数内重复定义、压缩主函数行数
const sortItemsByOrder = (items: EnumItemDTO[]): EnumItemDTO[] => {
  return [...items].sort((a, b) => (a?.enumOrder ?? 0) - (b?.enumOrder ?? 0));
};

// 模块级工具函数：将枚举数据按类型分组
const groupEnumsByType = (items: Record<string, EnumItemDTO[]>): EnumItemGrouped => {
  return items as unknown as EnumItemGrouped;
};

// 工厂函数：基于 enumsGroup 构造各类读取器，避免主 Hook 函数体过长
const buildFormattedEnums =
  (enumsGroup: EnumItemGrouped) =>
    (enumType: string): Record<string, string> => {
      const items = enumsGroup[enumType];
      if (!items) return {};
      return sortItemsByOrder(items).reduce(
        (acc, item) => {
          acc[`${item?.enumValue ?? ''}`] = item?.displayName ?? '';
          return acc;
        },
      {} as Record<string, string>,
      );
    };

const buildEnumOptions =
  (enumsGroup: EnumItemGrouped) =>
    (enumType: string): SelectOption[] => {
      const items = enumsGroup[enumType];
      if (!items) return [];
      return sortItemsByOrder(items).map((item) => ({
        value: `${item?.enumValue ?? ''}`,
        label: item?.displayName ?? '',
      }));
    };

const buildEnumDescription =
  (enumsGroup: EnumItemGrouped) =>
    (enumType: string, value: string): string => {
      const items = enumsGroup[enumType];
      if (!items || !value) return '';
      const hit = items.find((item) => `${item?.enumValue ?? ''}` === value);
      return hit?.description ?? '';
    };

const buildEnumOptionsWithDescription =
  (enumsGroup: EnumItemGrouped) =>
    (enumType: string): SelectOptionWithDescription[] => {
      const items = enumsGroup[enumType];
      if (!items) return [];
      return sortItemsByOrder(items).map((item) => ({
        value: `${item?.enumValue ?? ''}`,
        label: item?.displayName ?? '',
        description: item?.description ?? '',
      }));
    };

export default () => {
  // 枚举数据状态
  const [enumsGroup, setEnumsGroup] = useState<EnumItemGrouped>(() => {
    // 初始化时从localStorage获取枚举数据
    const cachedEnums = localStorage.getItem('enumsGroup');

    if (cachedEnums) {
      try {
        return JSON.parse(cachedEnums);
      } catch (error) {
        console.error('解析缓存的枚举数据失败:', error);
        return {};
      }
    }
    return {};
  });

  // 加载枚举数据
  const loadEnums = useCallback(async () => {
    try {
      const enumsRes = await post<any>({ url: '/api/v1/enums/list', data: {} });
      if (enumsRes.data) {
        const groupedEnums = groupEnumsByType(enumsRes.data);

        setEnumsGroup(groupedEnums);

        localStorage.setItem('enumsGroup', JSON.stringify(groupedEnums));

        return groupedEnums;
      }

      return enumsGroup;
    } catch (error) {
      console.error('加载枚举数据失败:', error);
      return enumsGroup;
    }
  }, [enumsGroup]);

  // 清除枚举数据缓存
  const clearEnums = useCallback(() => {
    setEnumsGroup({});
    localStorage.removeItem('enumsGroup');
  }, []);

  // 通过工厂函数构造读取器，主 Hook 函数体保持简洁
  const getFormattedEnums = useCallback(buildFormattedEnums(enumsGroup), [enumsGroup]);
  const getEnumOptions = useCallback(buildEnumOptions(enumsGroup), [enumsGroup]);
  const getEnumDescription = useCallback(buildEnumDescription(enumsGroup), [enumsGroup]);
  const getEnumOptionsWithDescription = useCallback(buildEnumOptionsWithDescription(enumsGroup), [
    enumsGroup,
  ]);

  // 获取状态的中文名称
  const getStatusName = useCallback(
    (statusCode: string, enumType: string = 'TaskState') => {
      if (!statusCode) return '-';
      const statusEnum = getFormattedEnums(enumType);
      return statusEnum[statusCode] || statusCode;
    },
    [getFormattedEnums],
  );

  // 初始化时，如果没有缓存数据，则加载枚举数据
  useEffect(() => {
    if (Object.keys(enumsGroup).length === 0) {
      loadEnums();
    }
  }, []);

  return {
    enumsGroup,
    loadEnums,
    clearEnums,
    getFormattedEnums,
    getEnumOptions,
    getEnumDescription,
    getEnumOptionsWithDescription,
    getStatusName,
  };
};
