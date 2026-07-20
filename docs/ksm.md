# KSM工单系统对接文档

## 一、对接流程概述

当KSM系统新增或变更工单时，会主动推送通知到第三方系统。第三方系统收到通知后，需要通过三步API调用获取完整的工单详情。

```
KSM工单变更 → 推送通知 → 第三方接收 → 获取AppToken → 获取AccessToken → 查询工单详情
```

当工单状态为"已提交"（status=1）时，还需额外触发自动受理流程：

```
查询工单详情 → 工单接管（lockKsmOrder）→ 重新拉取详情 → 工单处理（handleKsmOrder）→ 再次拉取详情 → 回写飞书
```

---

## 二、接收KSM推送通知

### 2.1 第三方需要提供的接口

**重要：第三方需要提供一个POST接口用于接收KSM的工单通知推送**

- **接口类型：** POST
- **Content-Type：** application/json
- **接口地址：** 由第三方提供，需要提前配置到KSM系统中
- **推送方式：** KSM系统主动推送，通知数据放在请求Body中

### 2.2 通知触发时机
- 工单新增时
- 工单状态变更时

### 2.3 推送通知数据格式

KSM系统会向第三方提供的接口发送POST请求，Body内容如下：

```json
{
  "noticeNum": "2411682884910966784",
  "subscribeNum": "ksm_feedback_change",
  "callbackUrl": "https://ierpuat.kingdee.com/ierp/kapi/app/open/subscribeCallback",
  "id": "4A71071A009482FEE06314C912AC57D1"
}
```

### 2.4 关键字段说明
| 字段 | 说明 | 用途 |
|------|------|------|
| noticeNum | 通知编号 | 唯一标识本次通知 |
| subscribeNum | 订阅类型 | 固定值：ksm_feedback_change |
| id | 工单ID | 用于后续查询工单详情（重要） |
| callbackUrl | 回调地址 | KSM系统的回调接口地址 |

### 2.5 接口要求
1. **快速响应**：建议收到通知后立即返回200状态码，避免超时
2. **异步处理**：工单详情查询等耗时操作建议异步处理
3. **幂等性**：同一个noticeNum可能会重复推送，需要做幂等处理
4. **日志记录**：建议记录所有推送通知，便于问题排查
5. **错误处理**：接口异常时返回明确的错误信息

---

## 三、鉴权流程（三步鉴权）

收到通知后，需要按顺序调用以下三个接口。**每次调用业务接口前都需重新获取，不做缓存。**

### 步骤1：获取AppToken

**接口地址：** `POST /ierp/api/getAppToken.do`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| appId | String | 是 | 应用ID |
| appSecuret | String | 是 | 应用密钥（注意拼写：appSecuret） |
| tenantid | String | 是 | 租户ID（SIT环境传空字符串） |
| accountId | String | 是 | 账号ID（SIT环境传空字符串） |
| language | String | 是 | 语言，固定传 zh_CN |

**请求示例：**
```json
{
  "appId": "Fa_Piao_Yun",
  "appSecuret": "Fa_Piao_Yun12345678",
  "tenantid": "ierp",
  "accountId": "836902273102643200",
  "language": "zh_CN"
}
```

**返回示例：**
```json
{
  "data": {
    "app_token": "2ed3dbd1-e79a-478c-8d96-9ec42f9a8846"
  }
}
```

> ⚠️ 注意：token 在 `data.app_token` 下，不是顶层字段。

---

### 步骤2：获取AccessToken

**接口地址：** `POST /ierp/api/login.do`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user | String | 是 | 用户名 |
| apptoken | String | 是 | 步骤1获取的 app_token |
| tenantid | String | 是 | 租户ID（SIT环境传空字符串） |
| accountId | String | 是 | 账号ID（SIT环境传空字符串） |
| usertype | String | 是 | 固定传 UserName |
| language | String | 是 | 固定传 zh_CN |

**请求示例：**
```json
{
  "user": "fapiaoyun",
  "apptoken": "2ed3dbd1-e79a-478c-8d96-9ec42f9a8846",
  "tenantid": "ierp",
  "accountId": "836902273102643200",
  "usertype": "UserName",
  "language": "zh_CN"
}
```

**返回示例：**
```json
{
  "data": {
    "access_token": "836902273102643200_t8KOh4bwN8aM8cXef6xlR8rBVfISKhmNDT5kWUT74VXjIo3rb3qCbaVfqMaH44s6E7ITWC72jc6R2pm4tJQKCPS3dF1jnQKR27pl03"
  }
}
```

> ⚠️ 注意：token 在 `data.access_token` 下，不是顶层字段。

---

### 步骤3：查询工单详情

**接口地址：** `POST /ierp/kapi/app/open/subscribeCallback?access_token={access_token}`

**超时建议：** 60秒（默认10秒不够）

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| access_token | String | 是 | URL参数，步骤2获取的AccessToken |
| noticeNum | String | 是 | 通知编号（来自推送通知） |
| subscribeNum | String | 是 | 订阅类型（来自推送通知） |
| appId | String | 是 | 固定传 ksm_wo |
| callbackUrl | String | 是 | 回调地址（来自推送通知） |
| id | String | 是 | 工单ID（来自推送通知） |

**请求示例：**
```json
{
  "noticeNum": "2411682884910966784",
  "subscribeNum": "ksm_feedback_change",
  "appId": "ksm_wo",
  "callbackUrl": "https://ierpuat.kingdee.com/ierp/kapi/app/open/subscribeCallback",
  "id": "4A71071A009482FEE06314C912AC57D1"
}
```

**返回示例：**
```json
{
  "success": true,
  "status": true,
  "errorCode": "success",
  "data": {
    "billId": "4A71071A009482FEE06314C912AC57D1",
    "billNumber": "R20260210-0001",
    "title": "李创测试20260210",
    "source": "KSM工单",
    "status": "1",
    "urgency": "5",
    "feedbackType": "8",
    "feedbackUser": "李创",
    "feedbackPhone": "17302693960",
    "feedbackTel": "",
    "feedbackEmail": "",
    "problem": "李创测试202602101111111111",
    "createDateTime": "2026-02-10 11:42:00",
    "updateDateTime": "2026-02-10 11:42:00",
    "parentNum": "",
    "product": {
      "id": "ec0278dcdf3545bab627029fad1aa1ec",
      "number": "C28",
      "name": "金蝶发票云"
    },
    "module": {
      "id": "3cb53ab3719c4b81a1ea9a4a2da7812a",
      "number": "R011364",
      "name": "电子影像服务"
    },
    "version": {
      "id": "b69ba94fcff64f56a0628ae01f13b14a",
      "number": "5.0",
      "mainproductname": "电子影像服务"
    },
    "customerInfo": {
      "customerNumber": "KHNB000000-00001",
      "customerName": "内部客户",
      "linkman": "李创",
      "mobile": "17302693960",
      "phone": "",
      "email": "2421349002@qq.com",
      "serviceLevel": "22"
    },
    "node": {
      "id": "749CA298F57942678C5DC5E6DD0C74B5",
      "code": "N004",
      "name": "受理",
      "stepid": "1"
    },
    "evaluateInfo": {
      "isSolved": "",
      "serveSatisfy": "",
      "serveTimely": "",
      "fservicequality": "",
      "advice": "",
      "isBack": ""
    },
    "handleSteps": [
      {
        "handleDateTime": "2026-02-10 11:42:00",
        "nodeName": "提交",
        "nodeDefCode": "N001",
        "nodeStatus": 1,
        "duringtime": 0,
        "dealopinion": "反馈提交",
        "delayReason": "",
        "releaseDate": "",
        "patchVersion": "",
        "patchNumber": "",
        "assignUser": {
          "id": 4088135,
          "number": "38311",
          "name": "panda_li",
          "realname": "李创",
          "mobile": "17302693960"
        }
      }
    ]
  }
}
```

---

## 四、工单接管接口（lockKsmOrder）

工单状态为"已提交"（status=1）时，调用此接口将工单接管到指定人员名下。

**接口地址：** `POST /ierp/kapi/v2/kded/kded_wos/lockKsmOrder?access_token={access_token}`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| billId | String | 是 | 工单ID |
| account | String | 是 | 接管人账号（传姓名） |
| accountName | String | 是 | 接管人姓名 |
| accountNumber | String | 是 | 接管人工号（从飞书员工搜索接口获取） |
| dealOpinion | String | 是 | 处理意见，固定传"已受理，工单人员分析处理中" |

**请求示例：**
```json
{
  "billId": "4A71071A009482FEE06314C912AC57D1",
  "account": "张三",
  "accountName": "张三",
  "accountNumber": "10086",
  "dealOpinion": "已受理，工单人员分析处理中"
}
```

**返回示例（成功）：**
```json
{
  "status": true,
  "message": "操作成功"
}
```

**返回示例（失败）：**
```json
{
  "status": false,
  "message": "工单已被接管"
}
```

> ⚠️ 注意：HTTP 200 不代表业务成功，必须判断返回体的 `status` 字段是否为 `true`。

---

## 五、工单处理接口（handleKsmOrder）

接管成功后，重新拉取最新工单详情，再调用此接口完成工单处理。

**接口地址：** `POST /ierp/kapi/v2/kded/kded_wos/handleKsmOrder?access_token={access_token}`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| billId | String | 是 | 工单ID（接管后重新拉取的最新数据） |
| account | String | 是 | 处理人账号（传姓名） |
| accountName | String | 是 | 处理人姓名 |
| accountNumber | String | 是 | 处理人工号 |
| linkman | String | 是 | 联系人（传处理人姓名） |
| email | String | 是 | 处理人邮箱（从飞书员工搜索接口获取） |
| mobile | String | 是 | 处理人手机（从飞书员工搜索接口获取） |
| productId | String | 是 | 产品ID（取接管后最新工单详情的 product.id） |
| versionId | String | 是 | 版本ID（取接管后最新工单详情的 version.id） |
| moduleId | String | 是 | 模块ID（取接管后最新工单详情的 module.id） |
| backType | String | 是 | 流转类型（取工单详情的 feedbackType，转为字符串） |
| isDeal | String | 是 | 固定传空字符串 `""` |
| dealOpinion | String | 是 | 处理意见，固定传"工单人员分析处理中" |
| dealMethod | String | 否 | 处理方式，传空字符串 |
| billType | String | 否 | 工单类型，传空字符串 |
| handleInfo.currentNodeID | String | 是 | 当前节点ID（取接管后最新工单详情的 node.id） |

**请求示例：**
```json
{
  "billId": "4A71071A009482FEE06314C912AC57D1",
  "account": "张三",
  "accountName": "张三",
  "accountNumber": "10086",
  "linkman": "张三",
  "email": "zhangsan@example.com",
  "mobile": "13800138000",
  "productId": "ec0278dcdf3545bab627029fad1aa1ec",
  "versionId": "b69ba94fcff64f56a0628ae01f13b14a",
  "moduleId": "3cb53ab3719c4b81a1ea9a4a2da7812a",
  "backType": "8",
  "isDeal": "",
  "dealOpinion": "工单人员分析处理中",
  "dealMethod": "",
  "billType": "",
  "handleInfo": {
    "currentNodeID": "749CA298F57942678C5DC5E6DD0C74B5"
  }
}
```

**返回示例（成功）：**
```json
{
  "status": true,
  "message": "操作成功"
}
```

> ⚠️ 注意：HTTP 200 不代表业务成功，必须判断返回体的 `status` 字段是否为 `true`。
> ⚠️ 注意：`isDeal` 必须传空字符串，不能传 `"1"` 或其他值。

---

## 六、工单详情字段说明

### 6.1 基本信息
| 字段 | 类型 | 说明 |
|------|------|------|
| billId | String | 工单ID（唯一标识） |
| billNumber | String | 工单编号（如 R20260210-0001） |
| title | String | 工单标题 |
| source | String | 来源（KSM工单） |
| status | String | 工单状态（见枚举表） |
| urgency | String | 紧急程度（见枚举表） |
| feedbackType | String | 流转类型（见枚举表） |
| feedbackUser | String | 反馈人姓名 |
| feedbackPhone | String | 反馈人手机 |
| feedbackTel | String | 反馈人电话 |
| feedbackEmail | String | 反馈人邮箱 |
| problem | String | 问题描述 |
| createDateTime | String | 创建时间（格式：yyyy-MM-dd HH:mm:ss） |
| updateDateTime | String | 更新时间（格式：yyyy-MM-dd HH:mm:ss） |
| parentNum | String | 父工单编号 |

### 6.2 产品信息
| 字段 | 类型 | 说明 |
|------|------|------|
| product.id | String | 产品ID（接管/处理接口需要） |
| product.number | String | 产品编码 |
| product.name | String | 产品名称 |
| module.id | String | 模块ID（接管/处理接口需要） |
| module.number | String | 模块编码 |
| module.name | String | 模块名称 |
| version.id | String | 版本ID（接管/处理接口需要） |
| version.number | String | 版本号 |
| version.mainproductname | String | 主产品名称 |

### 6.3 客户信息
| 字段 | 类型 | 说明 |
|------|------|------|
| customerInfo.customerNumber | String | 客户编码 |
| customerInfo.customerName | String | 客户名称 |
| customerInfo.linkman | String | 联系人 |
| customerInfo.mobile | String | 联系人手机 |
| customerInfo.phone | String | 联系电话 |
| customerInfo.email | String | 联系邮箱 |
| customerInfo.serviceLevel | String | KSM服务级别 |

### 6.4 处理节点信息
| 字段 | 类型 | 说明 |
|------|------|------|
| node.id | String | 当前节点ID（接管/处理接口需要） |
| node.code | String | 节点编码 |
| node.name | String | 节点名称（如"受理"） |
| node.stepid | String | 步骤ID |

### 6.5 评价信息
| 字段 | 类型 | 说明 |
|------|------|------|
| evaluateInfo.isSolved | String | 问题是否解决 |
| evaluateInfo.serveSatisfy | String | 服务整体满意度 |
| evaluateInfo.serveTimely | String | 服务及时性 |
| evaluateInfo.fservicequality | String | 服务质量 |
| evaluateInfo.advice | String | 意见与建议 |
| evaluateInfo.isBack | String | 是否退单 |

### 6.6 处理步骤（handleSteps）
每个步骤包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| handleDateTime | String | 处理时间（格式：yyyy-MM-dd HH:mm:ss） |
| nodeName | String | 处理节点名称 |
| nodeDefCode | String | 节点编码 |
| nodeStatus | Integer | 节点状态（0=待处理，1=处理完成） |
| duringtime | Integer | 环节耗时（分钟） |
| dealopinion | String | 处理说明 |
| delayReason | String | 内部注释 |
| releaseDate | String | 预计发布日 |
| patchVersion | String | 补丁版本号 |
| patchNumber | String | 补丁编号 |
| assignUser.id | Integer | 处理人ID |
| assignUser.number | String | 处理人账号 |
| assignUser.name | String | 处理人用户名 |
| assignUser.realname | String | 处理人真实姓名（优先使用） |
| assignUser.mobile | String | 处理人手机 |

---

## 七、枚举值说明

### 工单状态（status）
| 值 | 含义 |
|----|------|
| 0 | 已保存 |
| 1 | 已提交 |
| 2 | 处理中 |
| 3 | 答复完成 |
| 4 | 处理完成 |
| 5 | 处理关闭 |
| 6 | 已退回 |

### 紧急程度（urgency）
| 值 | 含义 |
|----|------|
| -1 | 特急-致命 |
| 0 | 特急-严重 |
| 1 | 紧急 |
| 5 | 一般 |


### 流转类型（feedbackType）
| 值 | 含义 |
|----|------|
| 0 | 应用问题 |
| 1 | 数据处理支持 |
| 2 | 产品需求分析 |
| 3 | 产品程序错误分析 |
| 4 | 应用支持 |
| 5 | 环境与运维支持 |
| 6 | 紧急故障（红灯） |
| 7 | 定制开发支持 |
| 8 | 技术支持 |
| 9 | 产品性能分析 |

---

## 八、环境地址

| 环境 | 域名 |
|------|------|
| UAT | ierpuat.kingdee.com |
| SIT | ierpsit.kingdee.com |

> SIT 环境调用 getAppToken 和 login 时，`tenantid` 和 `accountId` 传空字符串。

---

## 八点五、补充资料接口（supplyKsmOrder）

**接口地址：** `POST /ierp/kapi/v2/kded/kded_wos/supplyKsmOrder?access_token={access_token}`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| billId | String | 是 | 工单ID |
| account | String | 是 | 处理人用户名 |
| accountNumber | String | 是 | 处理人工号 |
| accountName | String | 是 | 处理人姓名 |
| dealOpinion | String | 否 | 处理说明（最长4000字节） |
| handleDateTime | DateTime | 否 | 处理时间 |
| currentNodeID | String | 是 | 当前节点ID（取工单详情的 node.id） |

**请求示例：**
```json
{
  "billId": "4A71071A009482FEE06314C912AC57D1",
  "account": "张三",
  "accountNumber": "10086",
  "accountName": "张三",
  "dealOpinion": "补充说明内容",
  "currentNodeId": "749CA298F57942678C5DC5E6DD0C74B5"
}
```

**返回参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| errorCode | String | 错误编码 |
| message | String | 接口返回消息 |
| status | Boolean | 接口调用状态（true=成功） |

> ⚠️ 注意：HTTP 200 不代表业务成功，必须判断返回体的 `status` 字段是否为 `true`。

---

## 八点六、工单拆单接口（splitKsmOrder）

**接口地址：** `POST /ierp/kapi/v2/kded/kded_wos/splitKsmOrder?access_token={access_token}`

**请求参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| billId | String | 是 | 工单ID |
| splitFeedbackNumber | int | 是 | 拆单数量（2-15） |
| account | String | 是 | 处理人用户名 |
| accountNumber | String | 是 | 处理人工号 |
| accountName | String | 是 | 处理人姓名 |

**请求示例：**
```json
{
  "billId": "4A71071A009482FEE06314C912AC57D1",
  "splitFeedbackNumber": 2,
  "account": "张三",
  "accountNumber": "10086",
  "accountName": "张三"
}
```

**返回参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| errorCode | String | 错误编码 |
| message | String | 接口返回消息 |
| status | Boolean | 接口调用状态（true=成功） |
| data | JSON | 数据包 |

**data 参数说明：**
| 参数 | 类型 | 说明 |
|------|------|------|
| sourceBillId | String | 源单ID |
| sourceBillNo | String | 源单编码 |
| splitBillNoArry | array | 拆分后工单编号集合，示例 `["R20250111-0001", "R20250111-0002"]` |

**返回示例（成功）：**
```json
{
  "status": true,
  "message": "操作成功",
  "data": {
    "sourceBillId": "4A71071A009482FEE06314C912AC57D1",
    "sourceBillNo": "R20250111-0001",
    "splitBillNoArry": ["R20250111-0002", "R20250111-0003"]
  }
}
```

> ⚠️ 注意：HTTP 200 不代表业务成功，必须判断返回体的 `status` 字段是否为 `true`。

---

## 九、对接注意事项

1. **三步鉴权每次重新获取**：AppToken 和 AccessToken 不做缓存，每次调用业务接口前重新获取
2. **HTTP 200 ≠ 业务成功**：所有接口必须判断返回体的 `status` 字段是否为 `true`
3. **subscribeCallback 超时**：该接口响应较慢，超时时间需设为 60 秒
4. **接管后需重新拉取详情**：接管操作会改变工单数据，必须重新调用 subscribeCallback 获取最新数据
5. **处理后再次拉取详情**：处理操作同样会改变工单数据，回写飞书前需再次拉取
6. **isDeal 传空字符串**：handleKsmOrder 的 isDeal 字段必须传 `""`，不能传 `"1"`
7. **快速响应推送**：收到 KSM 推送后立即返回 200，耗时操作异步处理
8. **幂等性处理**：同一个 noticeNum 可能重复推送，需做去重处理
9. **响应结构注意**：getAppToken 返回的 token 在 `data.app_token`，login 返回的 token 在 `data.access_token`，均为嵌套结构
