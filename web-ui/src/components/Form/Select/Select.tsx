import { Select as JSelect, type SelectProps } from '@jlogi/ui';

const Select = (props: SelectProps) => {
  return <JSelect {...props} />;
};
export default Select;

// import { i18nMessages } from '@/utils/i18n';
// import { LeftOutlined, RightOutlined } from '@ant-design/icons';
// import type { SelectProps as BaseSelectProps } from 'antd';
// import { Button, Col, Divider, Input, Row, Select as BaseSelect, Space, Tooltip } from 'antd';
// import debounce from 'lodash/debounce';
// import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useState } from 'react';

// export interface OptionType {
//   value: string;
//   label: string;
//   code?: string;
//   name?: string;
//   [key: string]: any;
// }

// export interface FieldNames {
//   value?: string;
//   label?: string;
//   groupLabel?: string;
//   options?: string;
//   title?: string;
// }

// export interface FieldNamesProps extends FieldNames {
//   title?: string;
// }
// export interface CustomLabelInValueType {
//   key?: React.Key;
//   value: string | number;
//   label: React.ReactNode;
//   title?: string;
//   data?: any;
//   [key: string]: any;
// }

// export interface SelectProps
//   extends Omit<
//     BaseSelectProps,
//     'fieldNames' | 'labelRender' | 'onSearch' | 'filterOption' | 'showSearch'
//   > {
//   fetchOptions?: (params: {
//     keyWord: string;
//     page: number;
//     pageSize: number;
//     [key: string]: any;
//   }) => Promise<{
//     data: OptionType[];
//     totalCounts: number;
//   }>;
//   pageSize?: number;
//   defaultkeyWord?: string;
//   columnsWidth?: number[]; // 用于动态设置 Code 和 Name 列的宽度
//   labelFieldNames?: {
//     value?: string;
//     label?: string;
//     [key: string]: any;
//   };
//   fieldNames?: FieldNamesProps;
//   labelRender?: (value: CustomLabelInValueType, option?: OptionType) => React.ReactNode;
//   showMoreFields?: {
//     label: string;
//     field: string;
//   }[];
//   showSearchPanel?: boolean; // 是否展示搜索和分页面板，默认为true
//   disabled?: boolean;
//   hideCodeColumn?: boolean; // 是否隐藏 Code 列
//   hideNameColumn?: boolean; // 是否隐藏 Name 列
// }

// export interface SelectRef {
//   reload: () => void;
// }

// const Select = forwardRef<SelectRef, SelectProps>((props, ref) => {
//   const {
//     placeholder = i18nMessages('input.select', '请选择'),
//     style = { width: '200px' },
//     options = [],
//     fetchOptions,
//     pageSize = 10,
//     defaultkeyWord = '',
//     columnsWidth = [50, 120], // 默认宽度 [Code, Name]
//     labelFieldNames,
//     onChange,
//     labelRender,
//     showMoreFields,
//     showSearchPanel = false, // 默认展示搜索和分页面板
//     disabled = false,
//     hideCodeColumn = false, // 是否隐藏 Code 列
//     hideNameColumn = false, // 是否隐藏 Name 列
//     ...restProps
//   } = props;
//   const [searchValue, setSearchValue] = useState<string>(defaultkeyWord);
//   const [loading, setLoading] = useState<boolean>(false);
//   const [currentPage, setCurrentPage] = useState<number>(1);
//   const [totalCounts, setTotalCounts] = useState<number>(0);
//   const [optionList, setOptionList] = useState<OptionType[]>(options as OptionType[]);
//   const [currentOption, setCurrentOption] = useState<OptionType>();
//   // 加载数据
//   const loadData = async (page: number, keyWord: string) => {
//     if (!fetchOptions) return;

//     setLoading(true);
//     try {
//       const result = await fetchOptions({
//         keyWord,
//         page,
//         pageSize,
//       });
//       setOptionList(result.data);
//       setTotalCounts(result.totalCounts);
//     } catch (error) {
//       console.error('加载选项失败:', error);
//     } finally {
//       setLoading(false);
//     }
//   };

//   // 初始加载
//   useEffect(() => {
//     if (fetchOptions && !disabled) {
//       const rafId = requestAnimationFrame(() => {
//         loadData(currentPage, searchValue);
//       });
//       // Cleanup to cancel the requestAnimationFrame if the component unmounts
//       return () => cancelAnimationFrame(rafId);
//     }
//   }, [disabled, fetchOptions]);

//   // 处理搜索输入变化
//   const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
//     setSearchValue(e.target.value);
//   };

//   // 处理搜索按钮点击
//   const handleSearch = () => {
//     setCurrentPage(1); // 重置到第一页
//     loadData(1, searchValue);
//   };

//   // 处理键盘回车事件
//   const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
//     e.stopPropagation(); // 阻止事件冒泡到 Select 组件
//     if (e.key === 'Enter') {
//       handleSearch();
//     }
//   };

//   // 上一页
//   const handlePrevPage = () => {
//     if (currentPage > 1) {
//       const newPage = currentPage - 1;
//       setCurrentPage(newPage);
//       loadData(newPage, searchValue);
//     }
//   };

//   // 下一页
//   const handleNextPage = () => {
//     const totalPages = Math.ceil(totalCounts / pageSize);
//     if (currentPage < totalPages) {
//       const newPage = currentPage + 1;
//       setCurrentPage(newPage);
//       loadData(newPage, searchValue);
//     }
//   };

//   // 计算总页数
//   const totalPages = Math.ceil(totalCounts / pageSize);

//   // 判断是否可以点击上一页/下一页
//   const canPrev = currentPage > 1;
//   const canNext = currentPage < totalPages;

//   // 自定义选项渲染
//   const customOptionRender = (oriOption: any) => {
//     const option = oriOption as OptionType;
//     const value = option.value || '';
//     const name = option.name || option.label || '';
//     const valueStr = (labelFieldNames?.value ? option.data[labelFieldNames.value] : value) || '--';
//     return (
//       <div className="flex">
//         {!hideCodeColumn && (
//           <div
//             className="mr-12 w-50"
//             style={{
//               width: columnsWidth[0] || 50,
//             }}
//           >
//             <Tooltip title={valueStr} className="w-100p block text-ellipsis">
//               {valueStr}
//             </Tooltip>
//           </div>
//         )}
//         {!hideNameColumn && (
//           <div
//             className="flex-1 w-120 mr-12"
//             style={{
//               width: columnsWidth[1] || 120,
//             }}
//           >
//             <Tooltip title={name} className="w-100p block text-ellipsis">
//               {name}
//             </Tooltip>
//           </div>
//         )}

//         {showMoreFields?.map((item, index) => {
//           const str = option.data[item.field];
//           return (
//             <div
//               key={item.field}
//               className="w-120  mr-12"
//               style={{
//                 width: columnsWidth[index + 2] || 120,
//               }}
//             >
//               <Tooltip title={str} className="w-100p block text-ellipsis">
//                 {str}
//               </Tooltip>
//             </div>
//           );
//         })}
//       </div>
//     );
//   };

//   // 自定义 onChange 处理，确保返回完整的数据对象
//   const handleChange = (value: any, option: any) => {
//     // 当值被置空时，重新加载数据
//     if ((value === undefined || value === null || value === '') && fetchOptions) {
//       setCurrentPage(1);
//       loadData(1, '');
//     }

//     if (onChange) {
//       if (restProps.labelInValue && option) {
//         // 当使用 labelInValue 时，返回包含完整数据的对象
//         const enrichedValue = {
//           ...value,
//           ...option,
//         };

//         onChange(enrichedValue, option);
//       } else {
//         onChange(value, option);
//       }
//     }
//     setCurrentOption(option);
//   };

//   useImperativeHandle(ref, () => ({
//     reload: () => {
//       setCurrentPage(1);
//       loadData(1, searchValue);
//       if (onChange) {
//         onChange(undefined, undefined);
//       }
//     },
//   }));

//   const handleSelectSearch = useCallback(
//     debounce(async (value) => {
//       setSearchValue(value);
//       setCurrentPage(1); // 重置到第一页
//       await loadData(1, value);
//     }, 300),
//     [loadData],
//   );

//   return (
//     <BaseSelect
//       showSearch={true}
//       placeholder={placeholder}
//       style={style}
//       options={optionList}
//       filterOption={false}
//       loading={loading}
//       popupMatchSelectWidth={false}
//       optionLabelProp="label"
//       optionRender={customOptionRender}
//       onChange={handleChange}
//       labelRender={(labelInValueType) => {
//         if (labelRender) {
//           return labelRender(labelInValueType, currentOption);
//         }
//         return labelInValueType.label;
//       }}
//       optionFilterProp="children"
//       onSearch={handleSelectSearch}
//       popupRender={(menu) => (
//         <div>
//           <div className="flex py-10 px-12">
//             {!hideCodeColumn && (
//               <div
//                 className="w-62  mr-12"
//                 style={{
//                   width: columnsWidth[0] || 62,
//                 }}
//               >
//                 Code
//               </div>
//             )}
//             {!hideNameColumn && (
//               <div
//                 className="w-120 mr-12"
//                 style={{
//                   width: columnsWidth[1] || 120,
//                 }}
//               >
//                 Name
//               </div>
//             )}
//             {showMoreFields?.map((item, index) => (
//               <div
//                 key={item.field}
//                 className="w-120 mr-12  text-ellipsis"
//                 style={{
//                   width: columnsWidth[index + 2] || 120,
//                 }}
//               >
//                 {item.label}
//               </div>
//             ))}
//           </div>
//           <Divider className="my-0" />
//           {menu}
//           {showSearchPanel && (
//             <>
//               <Divider className="my-1" />
//               <div className="p-8">
//                 <Row gutter={8} className="flex flex-nowrap mb-8">
//                   <Col flex="auto">
//                     <Input
//                       placeholder={i18nMessages('enterContent', '请输入搜索内容')}
//                       value={searchValue}
//                       onChange={handleInputChange}
//                       onKeyDown={handleKeyDown}
//                     />
//                   </Col>
//                   <Col>
//                     <Button
//                       type="primary"
//                       size="small"
//                       onClick={handleSearch}
//                       loading={loading}
//                       className="h-full"
//                     >
//                       {i18nMessages('query', '搜索')}
//                     </Button>
//                   </Col>
//                 </Row>

//                 {fetchOptions && (
//                   <Row justify="space-between" align="middle">
//                     <Col>
//                       <span className="text-12">
//                         {totalCounts} {i18nMessages('numbers', '条')}，{i18nMessages('which', '第')}{' '}
//                         {currentPage}/{totalPages || 1} {i18nMessages('page', '页')}
//                       </span>
//                     </Col>
//                     <Col>
//                       <Space>
//                         <Button
//                           type="link"
//                           size="small"
//                           icon={<LeftOutlined />}
//                           disabled={!canPrev}
//                           onClick={handlePrevPage}
//                           className="p-0-4"
//                         />
//                         <Button
//                           type="link"
//                           size="small"
//                           icon={<RightOutlined />}
//                           disabled={!canNext}
//                           onClick={handleNextPage}
//                           className="p-0-4"
//                         />
//                       </Space>
//                     </Col>
//                   </Row>
//                 )}
//               </div>
//             </>
//           )}
//         </div>
//       )}
//       disabled={disabled}
//       {...restProps}
//     />
//   );
// });

// export default Select;
