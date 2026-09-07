import { SearchSelect as JSearchSelect, type SearchSelectProps } from '@jlogi/ui';

const SearchSelect = (props: SearchSelectProps) => {
  return <JSearchSelect {...props} />;
};
export default SearchSelect;

// import { baseType } from '@/services/enum';
// import { i18nMessages } from '@/utils/i18n';
// import { forwardRef, useCallback, useMemo } from 'react';
// import Select, { OptionType, SelectProps, SelectRef } from './Select';

// interface BaseInformationItem {
//   code: string;
//   name: string;
//   id: string;
//   [key: string]: string | undefined;
// }

// interface BasePaginationResponse<T = unknown> {
//   current: number;
//   size: number;
//   total: number;
//   totalCounts: number;
//   pages: number;
//   list: T[] | null;
// }

// interface BaseApiResponse<T = unknown> {
//   result?: T;
//   code?: string;
//   data?: T;
//   i18n?: boolean;
//   message?: string;
//   msgId?: string;
//   success?: boolean;
// }

// interface BaseInformationRequest {
//   keyWord?: string;
//   page?: number;
//   pageSize?: number;
//   [key: string]: any;
// }

// // 动态参数接口
// interface Params {
//   [key: string]: string | number | boolean | undefined;
// }

// // 组件 Props 接口
// export interface SearchSelectProps extends Omit<SelectProps, 'fetchOptions'> {
//   url: string;
//   params?: Params;
//   fetchOptions?: (
//     type: string | number,
//     data: Partial<BaseInformationRequest>,
//   ) => Promise<BaseApiResponse<BasePaginationResponse<BaseInformationItem>>>;
// }

// const SearchSelect = forwardRef<SelectRef, SearchSelectProps>(
//   ({ url, params = {}, fetchOptions = baseType, fieldNames, ...restProps }, ref) => {
//     // 使用 useMemo 稳定 params 对象，避免不必要的重新请求
//     const stableParams = useMemo(() => params, [JSON.stringify(params)]);

//     // 适配 baseType 到 PagedSearchSelect 的 fetchOptions
//     const handleFetchOptions = useCallback(
//       async (fetchParams: { keyWord: string; page: number; pageSize: number }) => {
//         if (!url) {
//           return { data: [], totalCounts: 0 };
//         }

//         try {
//           const result = await fetchOptions(url, {
//             keyWord: fetchParams.keyWord,
//             current: fetchParams.page,
//             pageSize: fetchParams.pageSize,
//             ...stableParams,
//           });

//           const list = result.data?.list || [];
//           const fieldNameValue = fieldNames?.value || 'code';
//           const fieldNameTitle = fieldNames?.title || 'title';
//           const fieldNameLabel = fieldNames?.label || 'name';
//           const options: OptionType[] = list.map((item) => {
//             const res = {
//               value: item[fieldNameValue] || item.code,
//               label: item[fieldNameLabel] || item.name,
//               title: item[fieldNameTitle] || item.name,
//               data: item,
//               ...item,
//             };
//             return res;
//           });

//           return {
//             data: options,
//             totalCounts: result.data?.totalCounts || 0,
//           };
//         } catch (error) {
//           return { data: [], totalCounts: 0 };
//         }
//       },
//       [url, stableParams, fetchOptions],
//     );
//     return (
//       <Select
//         ref={ref}
//         fieldNames={fieldNames}
//         placeholder={restProps.placeholder ?? i18nMessages('input.select', '请选择')}
//         style={restProps.style ?? { width: '100%' }}
//         fetchOptions={handleFetchOptions}
//         pageSize={restProps.pageSize ?? 10}
//         allowClear={restProps.allowClear ?? true}
//         {...restProps}
//       />
//     );
//   },
// );

// export default SearchSelect;
